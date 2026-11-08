"""Bounded offline incident replay; the model never receives the grader fields."""

import json
import time
from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class Case:
    id: str
    version: int
    incident: dict
    fixtures: dict


@dataclass(frozen=True)
class Agent:
    id: str
    instructions: str
    reasoning_effort: str
    max_requests: int
    max_tool_calls: int


def load_agent(path: Path) -> Agent:
    data = yaml.safe_load(path.read_text())
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise ValueError("Unsupported agent schema")
    if not isinstance(data.get("instructions"), str) or not data["instructions"].strip():
        raise ValueError("Agent instructions are required")
    limits = data.get("limits", {})
    if data.get("reasoning_effort") not in {"low", "medium", "high"}:
        raise ValueError("Unsupported reasoning effort")
    if not 1 <= limits.get("model_requests", 0) <= 20 or not 1 <= limits.get("tool_calls", 0) <= 30:
        raise ValueError("Invalid agent limits")
    return Agent(data["id"], data["instructions"], data["reasoning_effort"], limits["model_requests"], limits["tool_calls"])


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


def run_case(case: Case, agent: Agent, client, model: str, tracer) -> dict:
    """Run with an injected Responses client, which permits isolated contract tests."""
    tools = [
        {
            "type": "function",
            "name": name,
            "description": f"Return the recorded read-only {name} evidence for this incident.",
            "strict": True,
            "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        }
        for name in case.incident["available_tools"]
    ]
    conversation = [{"role": "user", "content": json.dumps(case.incident)}]
    calls = []
    started = time.monotonic()

    with tracer.start_as_current_span("agent.case") as span:
        span.set_attribute("langfuse.export", "true")
        span.set_attribute("agent.id", agent.id)
        span.set_attribute("agent.case.id", case.id)
        span.set_attribute("agent.case.version", case.version)
        span.set_attribute("gen_ai.request.model", model)

        for request_number in range(agent.max_requests):
            with tracer.start_as_current_span("agent.model_request") as model_span:
                model_span.set_attribute("agent.request.number", request_number + 1)
                response = client.responses.create(
                    model=model,
                    instructions=agent.instructions,
                    input=conversation,
                    tools=tools,
                    reasoning={"effort": agent.reasoning_effort},
                    store=False,
                    include=["reasoning.encrypted_content"],
                    timeout=60,
                )
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
                    "case_id": case.id,
                    "case_version": case.version,
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
                if call.name not in case.fixtures:
                    raise ValueError(f"Unknown tool: {call.name}")
                if json.loads(call.arguments) != {}:
                    raise ValueError(f"Unexpected arguments: {call.name}")
                with tracer.start_as_current_span("agent.tool") as tool_span:
                    tool_span.set_attribute("agent.tool.name", call.name)
                    result = case.fixtures[call.name]
                    calls.append(call.name)
                    conversation.append({
                        "type": "function_call_output",
                        "call_id": call.call_id,
                        "output": json.dumps(result),
                    })
                    if getattr(call, "caller", None) is not None:
                        conversation[-1]["caller"] = call.caller.model_dump(exclude_none=True)

        raise RuntimeError("Model request limit reached")
