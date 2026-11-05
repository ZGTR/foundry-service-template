"""The fakes the template SHIPS, exercised.

WHY A FAKE NEEDS A TEST AT ALL. A fake that answers wrongly is worse than no fake: every
suite built on it goes green about the wrong thing, and the defect surfaces only when the
real adapter replaces it. These two were reached by nothing in this repository -- they are
here for the service an author emits, and an untested fake is a fixture nobody has checked
handed to somebody who will trust it.

Found by `scripts/check_test_first.py`. That is the gate doing its job rather than an
author noticing.
"""

from __future__ import annotations

from foundry_contracts.principal import PrincipalMinter

from foundry_data_gateway.adapters.fake.auth import FakePrincipalAuth
from foundry_data_gateway.adapters.fake.transaction import RecordingTransaction
from foundry_data_gateway.ports.auth import PrincipalAuth
from foundry_data_gateway.ports.transaction import TransactionBoundary


def test_the_fake_auth_satisfies_the_port() -> None:
    assert isinstance(FakePrincipalAuth(), PrincipalAuth)


def test_it_ignores_headers_entirely() -> None:
    """The point of this fake: a test gets a principal without minting a token. If it ever
    started reading headers, every suite using it would silently depend on header shape."""
    fake = FakePrincipalAuth()

    assert fake.authenticate({}) == fake.authenticate({"authorization": "Bearer nonsense"})


def test_its_default_principal_carries_both_widget_scopes() -> None:
    """A default missing a scope makes every authorised path in a suite fail for a reason
    that looks like the code under test."""
    principal = FakePrincipalAuth().authenticate({})

    assert principal.scopes == frozenset({"widgets:read", "widgets:write"})
    assert principal.org_id == "org_test"
    assert principal.issued_by == "fake"


def test_an_explicit_principal_wins_over_the_default() -> None:
    supplied = PrincipalMinter(issuer="test").mint(
        org_id="org-other", subject_id="s", scopes={"widgets:read"}
    )

    assert FakePrincipalAuth(supplied).authenticate({}) is supplied


def test_the_recording_transaction_satisfies_the_boundary() -> None:
    assert isinstance(RecordingTransaction(), TransactionBoundary)


def test_it_counts_entries_so_a_test_can_assert_one_boundary_per_request() -> None:
    """The reason it is a RECORDING fake rather than a null one: the defect it exists to
    catch is a second transaction opened inside a request, which a null fake cannot see."""
    transaction = RecordingTransaction()

    assert transaction.entries == 0  # positive control: it starts at zero
    with transaction.enter():
        pass
    with transaction.enter():
        pass

    assert transaction.entries == 2


def test_it_counts_an_entry_that_raised() -> None:
    """A boundary that was entered and then failed WAS entered. Counting only successful
    exits would under-report exactly the interleavings worth testing."""
    transaction = RecordingTransaction()

    try:
        with transaction.enter():
            raise RuntimeError("boom")
    except RuntimeError:
        pass

    assert transaction.entries == 1
