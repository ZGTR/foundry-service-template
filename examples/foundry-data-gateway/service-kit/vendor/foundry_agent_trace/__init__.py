"""The tracing and metrics ports every Foundry service depends on, plus adapters.

A blackbox takes a ``Tracer`` and a ``Meter`` through its constructor and
defaults to ``NoopTracer`` and ``NoopMeter`` -- telemetry is always optional,
never a hard dependency, and never in the critical path of a caller's result.

Spans and metrics are separate ports rather than one ``Telemetry`` object
because their cost models differ sharply: a span is priced per export and a
metric per active series, so the attributes that are cheap on one are the
expensive ones on the other. ``metric_attributes`` is where that divergence is
made explicit.
"""

from foundry_agent_trace._meter import Counter, Histogram, Meter
from foundry_agent_trace._redact import DenyListRedactor, Redactor
from foundry_agent_trace._slo_buckets import (
    JOB_DURATION_SECONDS,
    PAYLOAD_BYTES,
    PROCESS_MEMORY_BYTES,
    REQUEST_DURATION_SECONDS,
)
from foundry_agent_trace._span import RecordedSpan, Span
from foundry_agent_trace._tracer import Tracer
from foundry_agent_trace.console import ConsoleTracer, Writer
from foundry_agent_trace.memory import InMemoryTracer
from foundry_agent_trace.memory_meter import InMemoryMeter, MetricPoint
from foundry_agent_trace.noop import NoopTracer
from foundry_agent_trace.noop_meter import NoopMeter
from foundry_agent_trace.otlp import OtelResource, OtlpTracer
from foundry_agent_trace.otlp_meter import OtlpMeter, metric_attributes
from foundry_agent_trace.propagation import (
    current_traceparent,
    inject_trace_context,
    remote_trace_context,
)

__all__ = [
    "JOB_DURATION_SECONDS",
    "PAYLOAD_BYTES",
    "PROCESS_MEMORY_BYTES",
    "REQUEST_DURATION_SECONDS",
    "ConsoleTracer",
    "Counter",
    "DenyListRedactor",
    "Histogram",
    "InMemoryMeter",
    "InMemoryTracer",
    "Meter",
    "MetricPoint",
    "NoopMeter",
    "NoopTracer",
    "OtelResource",
    "OtlpMeter",
    "OtlpTracer",
    "RecordedSpan",
    "Redactor",
    "Span",
    "Tracer",
    "Writer",
    "current_traceparent",
    "inject_trace_context",
    "metric_attributes",
    "remote_trace_context",
]
