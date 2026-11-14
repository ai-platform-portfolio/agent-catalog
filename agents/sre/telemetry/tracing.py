"""One trace provider for the hosted adapter or evaluation process.

Export is optional, asynchronous and bounded. Langfuse credentials belong to the
collector; the resource marker forwards the whole trace, including child spans.
"""
import os

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor


def configure(**_host_options):
    # The adapter supplies connection_string/log_level to its supported callback.
    # This component intentionally configures only the declared OTLP trace sink.
    # No prompt/tool content capture or automatic HTTP credential instrumentation.
    os.environ['OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT'] = 'false'
    provider = TracerProvider(resource=Resource.create({
        'service.name': os.getenv('OTEL_SERVICE_NAME', 'sre-agent'),
        'service.version': os.getenv('SOURCE_REVISION', 'local'),
        'langfuse.export': 'true',
    }))
    endpoint = os.getenv('OTEL_EXPORTER_OTLP_TRACES_ENDPOINT')
    if endpoint:
        provider.add_span_processor(BatchSpanProcessor(
            OTLPSpanExporter(endpoint=endpoint, timeout=2),
            max_queue_size=512, max_export_batch_size=128,
            schedule_delay_millis=1000, export_timeout_millis=3000,
        ))
    trace.set_tracer_provider(provider)


def shutdown():
    provider = trace.get_tracer_provider()
    if hasattr(provider, 'force_flush'):
        provider.force_flush(timeout_millis=3000)
        provider.shutdown()
