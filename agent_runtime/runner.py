"""Bounded offline incident replay; the model never receives the grader fields."""

import json
import time
from dataclasses import dataclass
from pathlib import Path

import yaml

from portfolio_agent_factory.manifest import Agent, load_agent


@dataclass(frozen=True)
class Case:
    id: str
    version: int
    incident: dict
    fixtures: dict


def load_case(path: Path) -> Case:
    data = yaml.safe_load(path.read_text())
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise ValueError("Unsupported case schema")
    incident = data.get("incident")
    fixtures = data.get("tool_fixtures")
    if not isinstance(incident, dict) or not isinstance(fixtures, dict):
        raise ValueError("Case needs incident and tool fixtures")
    names = incident.get("available_tools")
    if not isinstance(names, list) or not names or set(names) != set(fixtures):
        raise ValueError("Available tools must match fixture keys")
    return Case(str(data["id"]), int(data["version"]), incident, fixtures)


def run_investigation(incident: dict, evidence: dict, agent: Agent, client, model: str, tracer, case: Case | None = None) -> dict:
    """One bounded investigation; evidence is supplied by a trusted adapter."""
    if not isinstance(incident, dict) or not isinstance(evidence, dict):
        raise ValueError("Incident and evidence must be objects")
    if len(evidence) > agent.max_tool_calls:
        raise ValueError("Too many available tools")
    tools = [
        {
            "type": "function",
            "name": name,
            "description": f"Return the recorded read-only {name} evidence for this incident.",
            "strict": True,
            "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        }
        for name in evidence
    ]
    visible_incident = {key: value for key, value in incident.items() if key != "available_tools"}
    visible_incident["available_tools"] = list(evidence)
    conversation = [{"role": "user", "content": json.dumps(visible_incident)}]
    calls = []
    started = time.monotonic()

    with tracer.start_as_current_span("agent.case") as span:
        span.set_attribute("langfuse.export", "true")
        span.set_attribute("agent.id", agent.id)
        if case is not None:
            span.set_attribute("agent.case.id", case.id)
            span.set_attribute("agent.case.version", case.version)
        span.set_attribute("gen_ai.request.model", model)

        for request_number in range(agent.max_requests):
            with tracer.start_as_current_span("agent.model_request") as model_span:
                model_span.set_attribute("agent.request.number", request_number + 1)
                request = dict(
                    model=model,
                    instructions=agent.instructions,
                    input=conversation,
                    reasoning={"effort": agent.reasoning_effort},
                    store=False,
                    include=["reasoning.encrypted_content"],
                    timeout=60,
                )
                if tools:
                    request["tools"] = tools
                response = client.responses.create(**request)
                if response.status != "completed":
                    raise RuntimeError(f"Model response status: {response.status}")
                model_span.set_attribute("gen_ai.response.id", response.id)

            conversation.extend(item.model_dump(exclude_none=True) for item in response.output)
            requested = [item for item in response.output if item.type == "function_call"]
            if not requested:
                answer = response.output_text
                if not answer:
                    raise RuntimeError("Model returned no answer")
                span.set_attribute("agent.tool_calls", len(calls))
                return {
                    "model": model,
                    "answer": answer,
                    "tool_calls": calls,
                    "model_requests": request_number + 1,
                    "elapsed_seconds": round(time.monotonic() - started, 3),
                    "trace_id": f"{span.get_span_context().trace_id:032x}",
                }

            for call in requested:
                if len(calls) >= agent.max_tool_calls:
                    raise RuntimeError("Tool call limit reached")
                if call.name not in evidence:
                    raise ValueError(f"Unknown tool: {call.name}")
                if json.loads(call.arguments) != {}:
                    raise ValueError(f"Unexpected arguments: {call.name}")
                with tracer.start_as_current_span("agent.tool") as tool_span:
                    tool_span.set_attribute("agent.tool.name", call.name)
                    result = evidence[call.name]
                    calls.append(call.name)
                    conversation.append({
                        "type": "function_call_output",
                        "call_id": call.call_id,
                        "output": json.dumps(result),
                    })
                    if getattr(call, "caller", None) is not None:
                        conversation[-1]["caller"] = call.caller.model_dump(exclude_none=True)

        raise RuntimeError("Model request limit reached")


def run_case(case: Case, agent: Agent, client, model: str, tracer) -> dict:
    """Offline replay uses the same loop but keeps grader fields outside it."""
    result = run_investigation(case.incident, case.fixtures, agent, client, model, tracer, case)
    return {"case_id": case.id, "case_version": case.version, **result}


def run_incident(payload: dict, agent: Agent, client, model: str, tracer) -> dict:
    """Initial hosted handler: no caller-provided tools or untrusted fixtures."""
    if not isinstance(payload, dict) or set(payload) != {"incident"}:
        raise ValueError("Invocation must contain only incident")
    incident = payload["incident"]
    if not isinstance(incident, dict) or not incident:
        raise ValueError("Incident must be a non-empty object")
    if {"reference", "rubric", "tool_fixtures"} & set(incident):
        raise ValueError("Invocation contains grader or fixture fields")
    return run_investigation(incident, {}, agent, client, model, tracer)
