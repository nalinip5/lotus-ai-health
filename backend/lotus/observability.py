"""Tracing with a correlation id shared by every sync and async step of a transaction.

Every agent, tool and LLM call opens a span. Spans are written to the `traces`
collection (visible in the UI's "Agent runs" panel). Point an OpenTelemetry
exporter at Arize by setting LOTUS_OTEL_ENDPOINT; spans are mirrored there when
the opentelemetry SDK is installed. Prompts are not stored by default (PHI).
"""
from __future__ import annotations

import contextvars
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Optional

log = logging.getLogger("lotus")

correlation_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("correlation_id", default=None)
current_case: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("current_case", default=None)

_otel_tracer = None
if os.environ.get("LOTUS_OTEL_ENDPOINT"):
    try:  # optional dependency
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        provider = TracerProvider()
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=os.environ["LOTUS_OTEL_ENDPOINT"])))
        trace.set_tracer_provider(provider)
        _otel_tracer = trace.get_tracer("lotus")
    except Exception as exc:  # pragma: no cover
        log.warning("OpenTelemetry export disabled: %s", exc)


def new_correlation_id() -> str:
    return "corr-" + uuid.uuid4().hex[:12]


class Tracer:
    def __init__(self) -> None:
        self.store = None  # set by app container

    @asynccontextmanager
    async def span(self, name: str, kind: str = "tool", **attrs: Any) -> AsyncIterator[dict[str, Any]]:
        span: dict[str, Any] = {
            "id": "span-" + uuid.uuid4().hex[:10],
            "name": name,
            "kind": kind,  # agent | llm | tool | workflow | rule
            "correlation_id": correlation_id.get(),
            "case_id": current_case.get(),
            "attrs": {k: v for k, v in attrs.items() if v is not None},
            "status": "ok",
        }
        start = time.perf_counter()
        span["started_at"] = time.time()
        otel_cm = _otel_tracer.start_as_current_span(name) if _otel_tracer else None
        otel_span = otel_cm.__enter__() if otel_cm else None
        try:
            yield span
        except Exception as exc:
            span["status"] = "error"
            span["error"] = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            span["duration_ms"] = round((time.perf_counter() - start) * 1000, 1)
            if otel_span is not None:
                otel_span.set_attribute("lotus.correlation_id", span["correlation_id"] or "")
                otel_span.set_attribute("lotus.kind", kind)
                otel_cm.__exit__(None, None, None)
            if self.store is not None:
                self.store.put("traces", span["id"], span)


tracer = Tracer()
