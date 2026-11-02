"""The estate's shared histogram bucket boundaries.

These are not a default anyone should override casually. An SLO objective is
evaluated as ``sum(rate(..._bucket{le="2"}))`` over a bucket edge, so a
threshold that does not fall exactly on an edge cannot be computed -- it can
only be interpolated, and an interpolated error budget is not an error budget.
Every latency target the estate writes must therefore appear in this tuple.

The count is also the bill. A histogram costs ``len(boundaries) + 3`` active
series per unique attribute combination (one per bucket, plus ``_sum``,
``_count`` and the ``+Inf`` bucket), so ten routes across three status classes
on the 14-bucket OpenTelemetry default is roughly 510 series for one service.
These twelve edges are chosen to cover 1 ms to 300 s -- the estate's real range,
given a 29 s API Gateway ceiling and a 300 s self-hosted model timeout -- at
the smallest count that still lands every threshold the SLOs use on an edge.
"""

from __future__ import annotations

REQUEST_DURATION_SECONDS: tuple[float, ...] = (
    0.005,
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.0,
    5.0,
    10.0,
    29.0,
    120.0,
)
"""Server request duration. 2.0 is the estate's default latency objective edge;
29.0 is API Gateway's hard ceiling, so the bucket above it is exactly the
population that a Lambda behind API Gateway could not have served; 120.0 is the
threshold section 8's soak uses to decide whether a service needs Kubernetes."""

PAYLOAD_BYTES: tuple[float, ...] = (
    1024.0,
    65536.0,
    1048576.0,
    7864320.0,
    10485760.0,
)
"""Artifact and request payload size. 7864320 (7.5 MB) is where a 10 MiB body
actually collides with API Gateway once base64 inflation is applied, and
10485760 is ``MAX_ARTIFACT_BYTES_CEILING``. A single observation in the top
bucket is the measurement that justifies the presigned-PUT upload path."""
JOB_DURATION_SECONDS: tuple[float, ...] = (
    1.0,
    10.0,
    60.0,
    300.0,
    900.0,
    3600.0,
)
"""Wall-clock of one BACKGROUND JOB execution -- a Restate workflow attempt, not
an HTTP request. ``REQUEST_DURATION_SECONDS`` cannot answer this question: it
stops at 120.0, so every job above two minutes lands in one ``+Inf`` bucket and
no percentile above that point can be read off it at all.

Each edge is a decision boundary, not a round number:

* ``1.0`` -- below a second the attempt did no external work. A Restate REPLAY
  re-reads its journal and returns; separating those from real attempts is what
  stops replays from dragging the percentile down.
* ``10.0`` -- an attempt that fetched and projected a small artifact. The
  ordinary case, and the bucket a healthy corpus should sit in.
* ``60.0`` -- one minute, where a synchronous caller has to stop waiting and
  poll for status instead.
* ``300.0`` -- THE threshold. The platform plan sends a RAG job above five
  minutes to Kubernetes and keeps everything below it on Lambda. A threshold
  that is not an edge can only be interpolated, and an interpolated platform
  decision is not a measurement.
* ``900.0`` -- AWS Lambda's maximum execution time. Above this edge a Lambda
  cannot run the job at ALL, whatever the percentile says; that is a harder and
  separate fact from the 300 s one, so it gets its own bucket.
* ``3600.0`` -- an hour. A job here is not slow, it is a job whose corpus has
  outgrown its design, and the count is the signal to resize before tuning.

Six edges is nine series per attribute combination. PROPAGATION: this tuple
lives in the vendored ``foundry_agent_trace``, which the estate keeps
byte-identical across all NINE repos that carry it. It was added in
``rag-service`` first because that is where the instrument it serves lives;
the other eight copies must receive the same edit before any of them uses it,
or the package stops being one package.
"""

PROCESS_MEMORY_BYTES: tuple[float, ...] = (
    134217728.0,
    536870912.0,
    1073741824.0,
    3153920000.0,
    10737418240.0,
)
"""Peak resident set size of the process, in bytes, bucketed on AWS LAMBDA'S
OWN MEMORY TIERS so a reading translates directly into a configuration:

* ``134217728`` -- 128 MiB, Lambda's minimum.
* ``536870912`` -- 512 MiB.
* ``1073741824`` -- 1 GiB, the point past which memory stops being incidental.
* ``3153920000`` -- 3,008 MiB, the ceiling Lambda held for years and the size
  many accounts are still capped at.
* ``10737418240`` -- 10,240 MiB, Lambda's current maximum. An observation in the
  ``+Inf`` bucket above this edge is the second half of the platform plan's RAG
  threshold: a memory ceiling a Lambda cannot hold, whatever the duration says.

Same PROPAGATION note as above.
"""
