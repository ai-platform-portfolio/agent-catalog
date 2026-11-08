"""Run one recorded case or expose a health-only endpoint in AKS."""

import argparse
import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from .runner import load_agent, load_case, run_case


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/healthz":
            self.send_error(404)
            return
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["serve", "run"])
    parser.add_argument("--case", type=Path)
    parser.add_argument("--agent", type=Path, default=Path("/app/agents/sre.yaml"))
    args = parser.parse_args()

    if args.command == "serve":
        HTTPServer(("0.0.0.0", int(os.getenv("PORT", "8080"))), HealthHandler).serve_forever()
        return

    if args.case is None:
        parser.error("run requires --case")

    from azure.identity import DefaultAzureCredential, get_bearer_token_provider
    from openai import OpenAI
    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    endpoint = os.environ["AZURE_OPENAI_ENDPOINT"].rstrip("/")
    model = os.environ["AZURE_OPENAI_DEPLOYMENT"]
    token = get_bearer_token_provider(DefaultAzureCredential(), "https://cognitiveservices.azure.com/.default")
    client = OpenAI(base_url=f"{endpoint}/openai/v1/", api_key=token, max_retries=1)

    provider = TracerProvider(resource=Resource.create({
        "service.name": "agent-catalog",
        "langfuse.export": "true",
    }))
    if os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"):
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
    try:
        result = run_case(load_case(args.case), load_agent(args.agent), client, model, trace.get_tracer(__name__))
        print(json.dumps(result, indent=2))
    finally:
        provider.shutdown()


if __name__ == "__main__":
    main()
