"""Tracing with OpenTelemetry, into Application Insights, which the Foundry project's Tracing view reads.

Each watch (one viewer's match) is one trace: under it, every agent's work (the News agent's build-up, the Match
agent's reads, the Ammo agent's digs, the Referee's checks and bookings, the Memory, the report, the Memory Keeper),
every Content Safety check and every GPT-Live voice session. Microsoft Agent Framework adds the model calls inside
each step (model, tokens, timing). What people say is never recorded, only what each agent did and how long it took.

Off unless APPLICATIONINSIGHTS_CONNECTION_STRING is set.
"""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from typing import Iterator

from opentelemetry import trace

log = logging.getLogger(__name__)
tracer = trace.get_tracer("nemesys")


def setup() -> bool:
    """Send traces to Application Insights, and switch on Agent Framework's own instrumentation."""
    from .agents.llm import load_env
    load_env()
    connection = os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING")
    if not connection:
        return False
    try:
        from azure.monitor.opentelemetry import configure_azure_monitor
    except ImportError:
        log.warning("APPLICATIONINSIGHTS_CONNECTION_STRING is set but azure-monitor-opentelemetry isn't installed")
        return False
    os.environ.setdefault("OTEL_SERVICE_NAME", "nemesys")
    configure_azure_monitor(connection_string=connection, enable_live_metrics=False)
    from agent_framework.observability import enable_instrumentation
    enable_instrumentation(enable_sensitive_data=False)  # names, models, tokens and timings; never what was said
    log.info("Tracing to Application Insights")
    return True


def start_watch(watch_id: str, mode: str) -> trace.Span:
    """The root of one viewer's match."""
    return tracer.start_span("nemesys.watch", attributes={"nemesys.watch_id": watch_id, "nemesys.mode": mode})


@contextmanager
def step(parent: trace.Span | None, name: str, **attributes) -> Iterator[trace.Span]:
    """One piece of an agent's work, inside its watch's trace."""
    context = trace.set_span_in_context(parent) if parent is not None else None
    with tracer.start_as_current_span(name, context=context,
                                      attributes={k: v for k, v in attributes.items() if v is not None}) as span:
        yield span
