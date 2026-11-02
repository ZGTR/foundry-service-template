"""The one seam through which a service obtains a credential.

Every adapter that needs a password, an API key or a signing secret depends on
this port, never on `boto3` or `os.environ` directly. Two consequences the
template cares about:

* the value's SOURCE is a wiring decision (`container.py`), not a property of
  the code that consumes it, so a local run and a production run differ by one
  constructor argument rather than by a branch inside every consumer; and
* there is exactly one place to audit for "does a credential reach a log".

`get` raises rather than returning `None`. A missing credential is not a
degraded mode -- a service that starts without the secret it needs will fail on
its first real request instead, further from the cause.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


class SecretNotFound(RuntimeError):
    """Raised when a named secret does not resolve. Carries the NAME, never a value."""


@runtime_checkable
class SecretsPort(Protocol):
    def get(self, name: str) -> str:
        """Return the secret's value, or raise `SecretNotFound`."""
        ...
