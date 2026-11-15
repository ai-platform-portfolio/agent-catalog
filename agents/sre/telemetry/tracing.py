"""Caller settings for the shared observability package."""
from functools import partial
import os

from portfolio_agent_observability import configure as configure_shared, shutdown

configure = partial(
    configure_shared,
    service_name=os.getenv('OTEL_SERVICE_NAME') or os.getenv('FOUNDRY_AGENT_NAME', 'catalog-agent'),
    service_version=os.getenv('SOURCE_REVISION', 'local'),
    langfuse=True,
)
