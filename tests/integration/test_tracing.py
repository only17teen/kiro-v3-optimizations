"""Integration tests for OpenTelemetry tracing."""

import asyncio
import pytest
from engine.tracing import KiroTracer, SpanKind, TracingConfig


@pytest.mark.asyncio
async def test_tracer_start_end_span():
    tracer = KiroTracer(TracingConfig(console_exporter=True, sampler_ratio=1.0))
    await tracer.start()

    span = tracer.start_span("test-operation", SpanKind.SERVER)
    span.set_attribute("key", "value")
    span.add_event("event1", {"detail": "info"})
    await asyncio.sleep(0.01)
    tracer.end_span(span)

    await tracer.flush()
    await tracer.shutdown()

    assert span.duration_ms >= 10
    assert span.attributes["key"] == "value"
    assert len(span.events) == 1


@pytest.mark.asyncio
async def test_tracer_context_propagation():
    tracer = KiroTracer(TracingConfig(sampler_ratio=1.0))
    parent = tracer.start_span("parent", SpanKind.SERVER)

    from engine.tracing.otel import attach_context, get_current_context
    with attach_context(parent.context):
        child = tracer.start_span("child", SpanKind.INTERNAL)
        assert child.context.trace_id == parent.context.trace_id
        assert child.context.parent_span_id == parent.context.span_id
        tracer.end_span(child)

    tracer.end_span(parent)
    await tracer.shutdown()


@pytest.mark.asyncio
async def test_tracer_decorator():
    tracer = KiroTracer(TracingConfig(console_exporter=True, sampler_ratio=1.0))

    @tracer.trace(name="decorated-op", kind=SpanKind.CLIENT)
    async def my_async_func(x: int) -> int:
        await asyncio.sleep(0.01)
        return x * 2

    result = await my_async_func(5)
    assert result == 10

    await tracer.flush()
    await tracer.shutdown()


@pytest.mark.asyncio
async def test_tracer_span_context_manager():
    tracer = KiroTracer(TracingConfig(console_exporter=True, sampler_ratio=1.0))
    await tracer.start()

    async with tracer.span("cm-span", SpanKind.INTERNAL, attributes={"a": 1}) as span:
        await asyncio.sleep(0.01)
        assert span.attributes["a"] == 1

    await tracer.flush()
    await tracer.shutdown()


@pytest.mark.asyncio
async def test_w3c_traceparent_roundtrip():
    from engine.tracing.otel import SpanContext
    ctx = SpanContext(trace_id="abc123" * 8, span_id="def456" * 4, sampled=True)
    header = ctx.to_w3c_traceparent()
    parsed = SpanContext.from_w3c_traceparent(header)
    assert parsed is not None
    assert parsed.trace_id == ctx.trace_id
    assert parsed.span_id == ctx.span_id
    assert parsed.sampled is True
