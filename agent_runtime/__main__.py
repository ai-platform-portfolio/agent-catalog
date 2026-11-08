"""Validate, replay, publish, or serve a versioned agent."""

import argparse
import importlib
import json
import os
from pathlib import Path

from agent_factory.manifest import load_agent
from agent_factory.publish import create_draft, version_request


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["validate", "run", "publish", "serve"])
    parser.add_argument("--agent", type=Path)
    parser.add_argument("--case", type=Path)
    parser.add_argument("--image")
    parser.add_argument("--revision")
    parser.add_argument("--project-endpoint")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    if args.command == "serve":
        from .server import main as serve

        serve()
        return

    if args.command == "validate":
        manifests = [args.agent] if args.agent else sorted(Path("agents").glob("*.yaml"))
        if not manifests:
            parser.error("No agent manifests found")
        for path in manifests:
            agent = load_agent(path)
            module, function = agent.handler.split(":", 1)
            if not callable(getattr(importlib.import_module(module), function)):
                raise ValueError(f"Handler is not callable: {agent.handler}")
            print(f"Valid hosted agent: {agent.id}")
        return

    if args.agent is None:
        parser.error(f"{args.command} requires --agent")
    agent = load_agent(args.agent)

    if args.command == "publish":
        if not args.image or not args.revision:
            parser.error("publish requires --image and --revision")
        request = version_request(agent, args.image, args.revision)
        if args.apply:
            if not args.project_endpoint:
                parser.error("--apply requires --project-endpoint")
            version = create_draft(args.project_endpoint, request)
            print(json.dumps({"name": version.name, "version": version.version, "status": version.status}, default=str))
        else:
            print(json.dumps(request, indent=2))
        return

    if args.case is None:
        parser.error("run requires --case")

    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    from .runner import load_case, run_case
    from .model import model_client

    endpoint = os.environ["FOUNDRY_PROJECT_ENDPOINT"].rstrip("/")
    model = os.environ.get("MODEL_DEPLOYMENT_NAME", agent.model)
    client = model_client(endpoint)

    provider = TracerProvider(resource=Resource.create({"service.name": "agent-catalog"}))
    if os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"):
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
    try:
        result = run_case(load_case(args.case), agent, client, model, trace.get_tracer(__name__))
        print(json.dumps(result, indent=2))
    finally:
        provider.shutdown()


if __name__ == "__main__":
    main()
