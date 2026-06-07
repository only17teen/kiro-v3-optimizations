"""OpenTelemetry tracing with context propagation for Kiro v3."""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import functools
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Awaitable, Callable, Dict, List, Optional, Protocol, TypeVar, Union


class SpanKind(Enum):
    INTERNAL = auto()
    SERVER = auto()
    CLIENT = auto()
    PRODUCER = auto()
    CONSUMER = auto()


@dataclass(slots=True)
class SpanContext:
    trace_id: str
    span_id: str
    parent_span_id: Optional[str] = None
    sampled: bool = True
    baggage: Dict[str, str] = field(default_factory=dict)

    def to_w3c_traceparent(self) -> str:
        flags = "01" if self.sampled else "00"
        return f"00-{self.trace_id}-{self.span_id}-{flags}"

    @classmethod
    def from_w3c_traceparent(cls, header: str) -> Optional[SpanContext]:
        parts = header.split("-")
        if len(parts) != 4 or parts[0] != "00":
            return None
        return cls(
            trace_id=parts[1],
            span_id=parts[2],
            sampled=parts[3] == "01",
        )


@dataclass(slots=True)
class Span:
    name: str
    context: SpanContext
    kind: SpanKind
    start_time_ns: int
    end_time_ns: int = 0
    attributes: Dict[str, Any] = field(default_factory=dict)
    events: List[Dict[str, Any]] = field(default_factory=list)
    status: Optional[str] = None
    status_description: Optional[str] = None

    def set_attribute(self, key: str, value: Any) -> None:
        self.attributes[key] = value

    def add_event(self, name: str, attributes: Optional[Dict[str, Any]] = None) -> None:
        self.events.append({
            "name": name,
            "timestamp_ns": time.time_ns(),
            "attributes": attributes or {},
        })

    def set_status(self, status: str, description: Optional[str] = None) -> None:
        self.status = status
        self.status_description = description

    def end(self, timestamp_ns: Optional[int] = None) -> None:
        self.end_time_ns = timestamp_ns or time.time_ns()

    @property
    def duration_ms(self) -> float:
        if self.end_time_ns == 0:
            return (time.time_ns() - self.start_time_ns) / 1e6
        return (self.end_time_ns - self.start_time_ns) / 1e6


@dataclass
class TracingConfig:
    service_name: str = "kiro-v3"
    service_version: str = "3.0.0"
    sampler_ratio: float = 1.0
    max_attributes: int = 128
    max_events: int = 128
    export_batch_size: int = 512
    export_interval_ms: float = 5000.0
    jaeger_endpoint: Optional[str] = None
    otlp_endpoint: Optional[str] = None
    console_exporter: bool = False


class Exporter(Protocol):
    async def export(self, spans: List[Span]) -> None: ...


class ConsoleExporter:
    async def export(self, spans: List[Span]) -> None:
        for span in spans:
            attrs = " ".join(f"{k}={v}" for k, v in span.attributes.items())
            print(f"[TRACE] {span.name} {span.duration_ms:.2f}ms {span.status or ''} {attrs}")


class OTLPExporter:
    def __init__(self, endpoint: str) -> None:
        self.endpoint = endpoint.rstrip("/")

    async def export(self, spans: List[Span]) -> None:
        import aiohttp
        payload = {
            "resourceSpans": [{
                "resource": {
                    "attributes": [
                        {"key": "service.name", "value": {"stringValue": "kiro-v3"}}
                    ]
                },
                "scopeSpans": [{
                    "spans": [self._span_to_proto(s) for s in spans]
                }]
            }]
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.endpoint}/v1/traces",
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    if resp.status >= 300:
                        print(f"[TRACE] OTLP export failed: {resp.status}")
        except Exception as e:
            print(f"[TRACE] OTLP export error: {e}")

    def _span_to_proto(self, span: Span) -> Dict[str, Any]:
        return {
            "traceId": span.context.trace_id,
            "spanId": span.context.span_id,
            "parentSpanId": span.context.parent_span_id or "",
            "name": span.name,
            "kind": span.kind.name,
            "startTimeUnixNano": str(span.start_time_ns),
            "endTimeUnixNano": str(span.end_time_ns),
            "attributes": [{"key": k, "value": {"stringValue": str(v)}} for k, v in span.attributes.items()],
            "events": [{"name": e["name"], "timeUnixNano": str(e["timestamp_ns"]), "attributes": []} for e in span.events],
            "status": {"code": span.status or "UNSET"},
        }


_current_context: contextvars.ContextVar[Optional[SpanContext]] = contextvars.ContextVar("kiro_trace_ctx", default=None)


def get_current_context() -> Optional[SpanContext]:
    return _current_context.get()


@contextlib.contextmanager
def attach_context(ctx: SpanContext):
    token = _current_context.set(ctx)
    try:
        yield
    finally:
        _current_context.reset(token)


T = TypeVar("T")


class KiroTracer:
    def __init__(self, config: Optional[TracingConfig] = None) -> None:
        self.config = config or TracingConfig()
        self._exporters: List[Exporter] = []
        self._spans: List[Span] = []
        self._lock = asyncio.Lock()
        self._export_task: Optional[asyncio.Task] = None
        self._shutdown = False
        self._trace_id_counter = 0
        self._span_id_counter = 0

        if self.config.console_exporter:
            self._exporters.append(ConsoleExporter())
        if self.config.otlp_endpoint:
            self._exporters.append(OTLPExporter(self.config.otlp_endpoint))

    def _next_trace_id(self) -> str:
        self._trace_id_counter += 1
        return f"{self._trace_id_counter:032x}"

    def _next_span_id(self) -> str:
        self._span_id_counter += 1
        return f"{self._span_id_counter:016x}"

    def start_span(
        self,
        name: str,
        kind: SpanKind = SpanKind.INTERNAL,
        parent: Optional[SpanContext] = None,
        attributes: Optional[Dict[str, Any]] = None,
    ) -> Span:
        parent = parent or get_current_context()
        trace_id = parent.trace_id if parent else self._next_trace_id()
        span = Span(
            name=name,
            context=SpanContext(
                trace_id=trace_id,
                span_id=self._next_span_id(),
                parent_span_id=parent.span_id if parent else None,
                sampled=parent.sampled if parent else (self.config.sampler_ratio >= 1.0 or self._should_sample()),
            ),
            kind=kind,
            start_time_ns=time.time_ns(),
            attributes=dict(attributes) if attributes else {},
        )
        if span.context.sampled:
            attach_context(span.context)
        return span

    def _should_sample(self) -> bool:
        import random
        return random.random() < self.config.sampler_ratio

    def end_span(self, span: Span) -> None:
        span.end()
        if not span.context.sampled:
            return
        asyncio.create_task(self._enqueue_span(span))

    async def _enqueue_span(self, span: Span) -> None:
        async with self._lock:
            self._spans.append(span)
            if len(self._spans) >= self.config.export_batch_size:
                await self._flush_locked()

    async def _flush_locked(self) -> None:
        if not self._spans:
            return
        batch = self._spans[:self.config.export_batch_size]
        self._spans = self._spans[self.config.export_batch_size:]
        for exporter in self._exporters:
            try:
                await exporter.export(batch)
            except Exception:
                pass

    async def flush(self) -> None:
        async with self._lock:
            await self._flush_locked()

    async def start(self) -> None:
        if self.config.export_interval_ms > 0:
            self._export_task = asyncio.create_task(self._export_loop())

    async def _export_loop(self) -> None:
        while not self._shutdown:
            await asyncio.sleep(self.config.export_interval_ms / 1000.0)
            await self.flush()

    async def shutdown(self) -> None:
        self._shutdown = True
        if self._export_task:
            self._export_task.cancel()
            try:
                await self._export_task
            except asyncio.CancelledError:
                pass
        await self.flush()

    def trace(
        self,
        name: Optional[str] = None,
        kind: SpanKind = SpanKind.INTERNAL,
        attributes: Optional[Dict[str, Any]] = None,
    ) -> Callable[[Callable[..., T]], Callable[..., T]]:
        def decorator(func: Callable[..., T]) -> Callable[..., T]:
            span_name = name or func.__name__

            @functools.wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                span = self.start_span(span_name, kind, attributes=attributes)
                try:
                    result = await func(*args, **kwargs)
                    span.set_status("OK")
                    return result
                except Exception as e:
                    span.set_status("ERROR", str(e))
                    span.set_attribute("error.type", type(e).__name__)
                    span.set_attribute("error.message", str(e))
                    raise
                finally:
                    self.end_span(span)

            @functools.wraps(func)
            def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                span = self.start_span(span_name, kind, attributes=attributes)
                try:
                    result = func(*args, **kwargs)
                    span.set_status("OK")
                    return result
                except Exception as e:
                    span.set_status("ERROR", str(e))
                    span.set_attribute("error.type", type(e).__name__)
                    span.set_attribute("error.message", str(e))
                    raise
                finally:
                    self.end_span(span)

            if asyncio.iscoroutinefunction(func):
                return async_wrapper
            return sync_wrapper
        return decorator

    @contextlib.asynccontextmanager
    async def span(
        self,
        name: str,
        kind: SpanKind = SpanKind.INTERNAL,
        attributes: Optional[Dict[str, Any]] = None,
    ):
        span = self.start_span(name, kind, attributes=attributes)
        try:
            yield span
            span.set_status("OK")
        except Exception as e:
            span.set_status("ERROR", str(e))
            span.set_attribute("error.type", type(e).__name__)
            span.set_attribute("error.message", str(e))
            raise
        finally:
            self.end_span(span)
