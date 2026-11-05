# One image, two runtimes -- see src/foundry_service_template/main.py's docstring
# for the full explanation of how the same process satisfies both.
#
#   * ECS Fargate:   this image's CMD *is* the service. It binds $PORT and
#                     serves the ALB directly.
#   * Lambda (image
#     package type):  the AWS Lambda Web Adapter (the extra COPY --from
#                     below) intercepts each Lambda invocation, starts this
#                     exact CMD as a child process, and forwards the
#                     invocation to it as an HTTP request over localhost.
#
# Supply-chain discipline borrowed from an older backend service/Dockerfile
# (the better of that repo's two images -- see README.md's "Motivation"
# section for what its Lambda sibling got wrong and why that is the reason
# THIS project builds one image, not two):
#   * base pinned by digest, not a floating tag
#   * third-party deps installed with `pip install --require-hashes` against
#     a lockfile pip-compile generated (requirements/service.lock) -- this
#     package's OWN source is never pip-installed here; it is COPY'd
#     directly (see the runtime stage), so there is nothing local for a hash
#     to cover and nothing unpinned for a second, drifted install path to
#     sneak in
#   * explicit per-path COPY, never `COPY . .`
#   * chmod -R a=rX, then USER <numeric-uid>:<numeric-uid> -- no named user,
#     no shell, nothing world-writable
#
# THIS FILE LIVES IN `service-kit/docker/`, the template-owned kit (contract
# §5). Every Foundry service carries the same folder, and the generic files in
# it are byte-identical; a value that differs between services arrives as a
# build arg or an environment variable, never as a forked copy.
#
# `tests/shape/test_single_dockerfile.py` asserts there is exactly ONE
# Dockerfile in this package and that it installs from a hash-pinned
# lockfile -- the two-Dockerfile drift this borrows discipline from is a
# structural failure mode, not a one-off mistake, so the check makes a
# second Dockerfile appearing later mechanically impossible rather than a
# convention someone has to remember.
#
# ---------------------------------------------------------------------------
# GENERIC: NOTHING BELOW NAMES THIS SERVICE
# ---------------------------------------------------------------------------
# This file is byte-identical in every Foundry service, so every value that
# differs between them arrives as a build arg:
#
#   SERVICE_PACKAGE         which package under `src/` is the service. REQUIRED
#                           -- the build refuses rather than guessing, because a
#                           default would silently build the wrong service's
#                           source into an image whose name says otherwise.
#   HAS_MIGRATIONS          `yes` to copy `migrations/` and `alembic.ini` into
#                           the image, `no` (the default) to skip them.
#   EXTRA_SYSTEM_PACKAGES   apt packages a specific service genuinely needs
#                           (a database client library, an image codec). Empty
#                           by default, and `apt-get` is not run at all when it
#                           is empty, so the common case keeps the base image's
#                           exact layer set.
#
# `HAS_MIGRATIONS` IS A STAGE SELECTOR, NOT A SHELL CONDITIONAL, and the reason
# is worth writing down because the obvious spelling does not exist: a
# Dockerfile has no `COPY ... IF`, and the usual workaround -- copy
# unconditionally and delete afterwards -- FAILS THE BUILD on a service with no
# `migrations/` directory, because a `COPY` of an absent path is an error rather
# than a no-op. So the two outcomes are two stages, `migrations-yes` and
# `migrations-no`, and the final stage's `FROM` interpolates the arg to pick
# one. BuildKit builds only the stages the target depends on, so on a service
# with no migrations the `COPY migrations` is never reached and never evaluated.
# That is a REQUIREMENT, not an optimisation: under the legacy builder
# (`DOCKER_BUILDKIT=0`) every stage is built and the no-DB build breaks. Docker
# has defaulted to BuildKit since 23.0, and `service-kit/gates/
# check_folder_structure.py` asserts the pattern rather than the flag, since a
# builder choice made in someone's environment is not something a gate can see.

ARG HAS_MIGRATIONS=no

FROM python:3.12-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea AS builder

ARG SOURCE_DATE_EPOCH=0

WORKDIR /build

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements/service.lock requirements/service.lock
RUN test "${SOURCE_DATE_EPOCH}" -ge 0 \
    && pip install --no-cache-dir --require-hashes -r requirements/service.lock

# --- runtime stage -----------------------------------------------------

FROM python:3.12-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea AS runtime

# The Lambda Web Adapter: a static binary, inert unless the Lambda runtime
# invokes this container. Its presence costs nothing under ECS. Pinned by
# digest for the same reason the Python base is.
COPY --from=public.ecr.aws/awsguru/aws-lambda-adapter@sha256:e2653f741cd15851ba4f13f3cc47d29f2d14377c7d11737bfa272baa1b569007 /lambda-adapter /opt/extensions/lambda-adapter

COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH=/app \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080 \
    AWS_LWA_PORT=8080

# Service-specific apt packages, if any. The guard is not decoration: without
# it, an empty `EXTRA_SYSTEM_PACKAGES` would still run `apt-get update`, adding
# a layer and a network dependency to every build in the estate for the benefit
# of the services that need neither.
ARG EXTRA_SYSTEM_PACKAGES=""
RUN if [ -n "${EXTRA_SYSTEM_PACKAGES}" ]; then \
        apt-get update \
        && apt-get install -y --no-install-recommends ${EXTRA_SYSTEM_PACKAGES} \
        && rm -rf /var/lib/apt/lists/*; \
    fi

WORKDIR /app

# Which service this is. No default: an unset `SERVICE_PACKAGE` must fail the
# build, because the alternative is an image that builds, starts, answers
# `/health/live`, and serves the wrong service's code.
ARG SERVICE_PACKAGE
RUN test -n "${SERVICE_PACKAGE}" \
    || ( echo "SERVICE_PACKAGE is required: --build-arg SERVICE_PACKAGE=<the package under src/>" >&2 \
         && exit 1 )

# This package's own source -- COPY'd directly, never pip-installed, so
# `requirements/service.lock` only has to hash-pin genuine third-party
# dependencies. Explicit per-path, matching the discipline this borrows.
#
# ONE COPY OF THE WHOLE `service-kit/vendor/` TREE, not one line per package
# name. `foundry_contracts` and `foundry_agent_trace` are vendored
# unconditionally because they are universal rather than optional (measured
# 2026-09-18 across the nine services: six import `foundry_agent_trace`
# outside their own vendored copy -- governance 11 files, agent-runtime 14,
# llm-gateway 17, artifact-store/rag/model-foundry 2 each). A service that
# ALSO vendors a domain-specific package beside them (comms-core's
# `factbus` and `comms_contracts`) used to need a
# hand-edited, no-longer-generic Dockerfile to get it into the image -- the
# exact fork this file's own banner ("GENERIC: NOTHING BELOW NAMES THIS
# SERVICE") exists to rule out. `COPY <dir>/ <dest>/` copies a directory's
# CONTENTS, so this line puts every subdirectory of `service-kit/vendor/`
# (whatever VENDOR.json declares, checked by check_vendor_digests.py) at the
# same flat `/app/<package>` layout the two named lines produced, with no
# Dockerfile edit and no new build arg for the next package either.
COPY service-kit/vendor/ ./
COPY src/${SERVICE_PACKAGE} ${SERVICE_PACKAGE}
COPY service-kit/docker/entrypoint.sh entrypoint.sh
COPY service-kit/docker/healthcheck.sh healthcheck.sh
COPY service-kit/docker/wait-for.sh wait-for.sh

# --- the migrations selector -------------------------------------------
# Two stages, one per outcome; the final `FROM` below picks one by arg. Neither
# stage hardens the tree -- `chmod` has to run after the last `COPY`, so it
# lives in the selected stage.

FROM runtime AS migrations-no
# Nothing. A service with no Alembic tree gets the runtime stage unchanged, and
# the `COPY` in the sibling stage is never evaluated.

FROM runtime AS migrations-yes
ARG SERVICE_PACKAGE
COPY alembic.ini alembic.ini
COPY migrations migrations

# --- the selected image ------------------------------------------------

FROM migrations-${HAS_MIGRATIONS} AS service

ARG SERVICE_PACKAGE
ENV SERVICE_PACKAGE="${SERVICE_PACKAGE}"

RUN chmod -R a=rX /app \
    && chmod 0555 entrypoint.sh healthcheck.sh wait-for.sh \
    && mkdir -p /tmp \
    && chmod 1777 /tmp

USER 65532:65532
EXPOSE 8080

ENTRYPOINT ["/app/entrypoint.sh"]
