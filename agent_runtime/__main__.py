"""Replay a recorded SRE case against the configured model route."""

import argparse
import json
import os
from pathlib import Path

from portfolio_agent_factory.manifest import load_agent
from portfolio_agent_factory.model import model_client


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent", type=Path, required=True)
    parser.add_argument("--case", type=Path, required=True)
    args = parser.parse_args()

    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    from portfolio_agent_factory.evaluation import load_case, run_case

    agent = load_agent(args.agent)
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
