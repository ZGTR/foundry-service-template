"""The error vocabulary every blackbox shares.

Callers catch these; they never catch a provider SDK's exception type, because a
blackbox that leaks its adapter's exceptions has not actually encapsulated it.
"""

from __future__ import annotations


class FoundryError(Exception):
    """Base class for every error a blackbox raises deliberately."""


class ConfigurationError(FoundryError):
    """A constructor was handed a configuration that cannot work.

    Raised at construction time, never at call time. A blackbox that discovers a
    missing setting on its first request has deferred a startup failure into a
    user-visible one.
    """


class Unauthorized(FoundryError):
    """The caller could not be AUTHENTICATED. Maps to HTTP 401.

    Every explicit ``raise Unauthorized`` in this estate's services is this case:
    a missing or malformed ``Authorization`` header, a token whose signature does
    not verify, an unknown key id, absent identity headers. Measured 2026-09-15
    across the nine services -- 33 raise sites, all authentication.

    The one exception was this class's own ``CallerPrincipalV1.require``, which
    raises when a VERIFIED principal lacks a scope. That is authorisation, not
    authentication, and 401 tells the caller to go and authenticate when they
    already have: a read-only principal POSTing was told it was unauthenticated
    and would retry with the same valid token forever. It now raises ``Forbidden``.
    """


class Forbidden(Unauthorized):
    """The caller IS authenticated and is not permitted this operation. HTTP 403.

    A SUBCLASS of ``Unauthorized`` on purpose, and the choice is the whole
    migration. Every ``except Unauthorized`` in every service keeps catching this,
    and a service that has not yet registered a 403 handler falls through to its
    existing 401 one -- so splitting the meaning costs nothing anywhere it has not
    been wired up, and is not a flag day across seven vendored copies. A sibling
    class would have made each unwired service answer 500 on a missing scope,
    turning a wrong-but-harmless status into an outage.

    The cost of the subclassing, recorded rather than hidden: a handler registered
    for ``Unauthorized`` alone will swallow a ``Forbidden`` silently, so a service
    reading 401 on a missing scope has not been wired, not proven correct.
    """


class PolicyRefused(FoundryError):
    """A policy declined the operation before any external effect occurred.

    The distinguishing property is *before*: nothing was sent, nothing was billed,
    and a retry with different inputs is meaningful.
    """

    def __init__(self, reason: str, *, rule: str) -> None:
        super().__init__(f"{rule}: {reason}")
        self.reason = reason
        self.rule = rule


class ProviderFailed(FoundryError):
    """An external provider ANSWERED and the answer was a failure.

    The three-way distinction this completes, and why each member has to exist:

    * ``PolicyRefused`` -- OUR rule declined it. Nothing was sent, nothing billed.
    * ``PossibleSend`` -- we cannot tell whether the effect happened. Reconcile.
    * ``ProviderFailed`` -- the provider was reached and said no: a 429 throttle,
      an exhausted quota, a content filter, a malformed response. A retry is
      meaningful and carries no risk of a duplicate effect.

    Without this class the third case had nowhere to go. It was raised as an
    adapter-specific error, propagated through the gateway unchanged, and escaped
    ``AgentRuntime.run`` as a bare exception -- contradicting that package's own
    docstring ("nothing this package cannot classify escapes as a bare
    exception") and producing an ESCAPED agent inside a report that otherwise
    read as a pass. Measured 2026-09-04.

    Adapter-level failure types SUBCLASS this rather than being translated at the
    gateway, so a new provider adapter is classified by default: getting it wrong
    requires deriving from something else on purpose.
    """

    def __init__(self, message: str, *, provider: str | None = None) -> None:
        super().__init__(message)
        self.provider = provider


class PossibleSend(FoundryError):
    """An external effect may or may not have happened, and we cannot tell.

    This is the ambiguity class. A connection that dies after the request left the
    process but before the response arrived is neither a success nor a failure, and
    treating it as either is a bug: as success it loses work, as failure it
    double-charges. The caller must reconcile with an idempotency key.
    """

    def __init__(self, message: str, *, idempotency_key: str | None = None) -> None:
        super().__init__(message)
        self.idempotency_key = idempotency_key
