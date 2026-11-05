"""A REAL AWS HTTP surface for S3, SQS and Secrets Manager -- never a mock.

§7 forbids `MagicMock` and collaborator `monkeypatch` for anything that has a
port. The alternative it names is LocalStack, which the kit carries as a compose
FRAGMENT at `service-kit/docker/compose/localstack.yml` and every service
consumes through its own `deploy/compose.yml`. This kit provides that, with a
second path for the case the estate actually measures:

* **LocalStack** -- the production-parity path, used whenever something is
  listening on the endpoint. Declaring `localstack` in
  `[tool.foundry.test-resources]` is what makes the kit START it; an
  `include:` alone only makes it resolvable, which is how this template shipped
  a fragment no run had ever pulled.
* **`moto[server]`** -- the same idea without a daemon: `python -m moto.server`
  binds a real HTTP endpoint that speaks the same three APIs, and `boto3`
  connects to it over the network with real signing. It is a simulated SERVICE,
  not a patched object, so the "no mocks" rule holds. What it is NOT is
  production parity -- moto implements a subset, so a behaviour it gets wrong is
  a behaviour this kit will not catch. That is why LocalStack is preferred when
  it is available, and why `AwsKit.flavour` records which one ran.
* **neither reachable** -> `AwsKitUnavailable`, which the fixture turns into a
  NAMED skip. Never a silent pass; never `moto.mock_aws` as a decorator, which
  is a patch of the client object rather than a service on the wire.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Any

from foundry_testkit.resources import ResourceUnavailable, docker_available, tcp_open

__all__ = ["AwsKit", "AwsKitUnavailable", "start_aws_kit"]

#: The variable an operator sets to point this kit at an endpoint they chose.
ENDPOINT_ENV = "LOCALSTACK_ENDPOINT"
#: Where LocalStack listens when nobody has said otherwise. A CONVENTION, not a
#: choice, and the distinction is the whole of `_configured_endpoint()` below.
DEFAULT_ENDPOINT = "http://127.0.0.1:4566"
#: The compose entry point every emitted service has. `deploy/compose.yml`
#: `include:`s the kit's fragments, so this resolves `localstack` as a service
#: without the reader needing to know where the fragment lives. The previous
#: remedy named `docker/compose/localstack.yml`, a path that has not existed
#: since the kit move -- see this module's `start_aws_kit` docstring.
_START_IT = "`docker compose -f deploy/compose.yml up -d localstack`"
_REGION = "us-east-1"
_REMEDY = (
    "no AWS surface reachable. Either start LocalStack "
    f"({_START_IT}, needs a Docker daemon) "
    "or install the native fallback (`pip install 'moto[server]'`), which needs no daemon."
)


def _configured_endpoint() -> tuple[str, bool]:
    """The endpoint to try, and whether an OPERATOR chose it rather than the default.

    READ AT CALL TIME, never captured at import. This module is imported when
    pytest collects `tests/conftest.py`, and the kit's own `pytest_configure`
    publishes the resolved endpoint AFTER that -- so a module-level constant is
    a photograph of the environment taken before the thing it describes exists.

    The boolean is what makes the refusal below honest. `LOCALSTACK_ENDPOINT`
    unset and `LOCALSTACK_ENDPOINT` set to the conventional default are the same
    STRING and different INTENTIONS: the first is "wherever it normally is", the
    second is "this one, I mean it". Collapsing them is how the old message came
    to offer `unset LOCALSTACK_ENDPOINT` as a remedy for a value the code
    supplied itself, so unsetting it changed nothing and the operator was told
    to perform a no-op.
    """
    chosen = os.environ.get(ENDPOINT_ENV)
    if chosen:
        return chosen, True
    return DEFAULT_ENDPOINT, False


class AwsKitUnavailable(ResourceUnavailable):
    """Neither LocalStack nor moto-server could be reached. Carries the remedy."""


@dataclass
class AwsKit:
    """A live endpoint plus the boto3 clients pointed at it."""

    endpoint_url: str
    #: "localstack" or "moto-server". Recorded, not inferred: a report that does
    #: not say which surface ran cannot be read as production parity.
    flavour: str
    region: str = _REGION
    _process: subprocess.Popen[bytes] | None = None

    def client(self, service: str) -> Any:
        import boto3

        return boto3.client(
            service,
            region_name=self.region,
            endpoint_url=self.endpoint_url,
            aws_access_key_id="test",
            aws_secret_access_key="test",  # noqa: S106 -- deterministic test credentials
        )

    def load_object_fixture(self, fixture: dict[str, Any]) -> int:
        """Load `{"bucket": ..., "objects": [{"key":..., "body_b64":...}]}` into S3."""
        import base64

        bucket = fixture["bucket"]
        s3 = self.client("s3")
        s3.create_bucket(Bucket=bucket)
        for entry in fixture["objects"]:
            s3.put_object(
                Bucket=bucket,
                Key=entry["key"],
                Body=base64.b64decode(entry["body_b64"]),
            )
        return len(fixture["objects"])

    def close(self) -> None:
        if self._process is not None:
            self._process.terminate()
            try:
                self._process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._process.kill()
            self._process = None


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _start_moto_server(timeout: float = 20.0) -> AwsKit:
    try:
        import moto  # noqa: F401
    except ImportError as exc:
        raise AwsKitUnavailable(_REMEDY) from exc
    port = _free_port()
    process = subprocess.Popen(  # noqa: S603
        [sys.executable, "-m", "moto.server", "-p", str(port), "-H", "127.0.0.1"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise AwsKitUnavailable(f"moto.server exited during startup. {_REMEDY}")
        if tcp_open("127.0.0.1", port):
            return AwsKit(
                endpoint_url=f"http://127.0.0.1:{port}",
                flavour="moto-server",
                _process=process,
            )
        time.sleep(0.2)
    process.kill()
    raise AwsKitUnavailable(
        f"moto.server did not bind 127.0.0.1:{port} within {timeout}s. {_REMEDY}"
    )


def start_aws_kit() -> AwsKit:
    """Return a live AWS surface, preferring LocalStack. Refuses if neither runs.

    THE REFUSAL IS SCOPED TO AN ENDPOINT SOMEBODY ASKED FOR, and that is the fix
    for a remedy nobody could perform. This function used to refuse whenever a
    Docker daemon was running and nothing answered on 4566, telling the operator
    to `unset LOCALSTACK_ENDPOINT` -- while the module supplied that value as its
    own default, so unsetting it produced the identical string and the identical
    refusal. Both halves of the message were dead: the compose path had not
    existed since the kit move, and the fallback it offered was unreachable for
    EVERY value of the variable, including none. Measured on this branch before
    the change: four SQS arms and one Redis arm skipped at exit 0, on a machine
    where `moto[server]` was installed and would have served all four.

    So an endpoint the operator NAMED is honoured strictly -- if it does not
    answer, falling through to moto would hand back a surface they did not
    choose, and `AwsKit.flavour` would be the only place that said so. An
    endpoint nobody named is a convention, and a convention that is not
    listening is exactly the case the fallback exists for.
    """
    if shutil.which("python") is None and shutil.which(sys.executable) is None:
        raise AwsKitUnavailable(_REMEDY)
    endpoint, chosen_by_operator = _configured_endpoint()
    host_port = endpoint.rsplit("/", 1)[-1]
    host, _, port = host_port.partition(":")
    if port and tcp_open(host, int(port)):
        return AwsKit(endpoint_url=endpoint, flavour="localstack")
    if chosen_by_operator and docker_available():
        raise AwsKitUnavailable(
            f"a Docker daemon is available but nothing answers at {endpoint}, which was "
            f"set through {ENDPOINT_ENV}. Start it with {_START_IT}, point {ENDPOINT_ENV} "
            f"at a surface that is listening, or unset {ENDPOINT_ENV} to fall back to "
            "moto-server. Refusing to substitute a surface you did not choose."
        )
    return _start_moto_server()
