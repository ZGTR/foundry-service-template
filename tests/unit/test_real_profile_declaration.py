"""The kit real profile's PURE halves, each arm isolating one clause.

WHY THESE ARE UNIT ARMS AND THE REST ARE NOT. Everything here is a function over
a string, a path or a mapping: what `[tool.foundry.test-resources]` says, what a
checkout path hashes to, what a worker id becomes, which tables the mapping
declares. None of it is a fact about PostgreSQL, so making these wait on a
container would make them slow enough to be turned off. The claims that ARE about
PostgreSQL -- that the template has no sessions before the clone, that a full run
leaves `pg_stat_activity` where it found it -- live in
`tests/integration/test_real_profile_connection_accounting.py` and use a real
server, because those are exactly the claims a substitute cannot answer.

ONE FIXTURE PER CLAUSE. A single "bad pyproject" fixture tripping several
refusals at once would let any one of them be deleted while the arm stayed red,
which is what an unexercised clause looks like from the outside.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from foundry_testkit.real_profile import (
    RealProfile,
    RealResourcesUnavailable,
    compose_project_name,
    load_declaration,
    owned_tables,
    pin_fake_aws_credentials,
    worker_database_name,
)

ROOT = Path(__file__).resolve().parents[2]

#: HAND-WRITTEN, never derived from the artifact under test. Reading
#: `Base.metadata` here to build the expectation would make the assertion
#: `x == x` and hold for every possible mapping, including an empty one.
EXPECTED_TABLES = {"widgets", "outbox"}

PYPROJECT_WITH = """\
[project]
name = "worked-example"

[tool.foundry.test-resources]
resources = ["postgres", "localstack:s3,sqs"]
orm_metadata = "foundry_service_template.adapters.real.orm:Base"
"""


def _write(tmp_path: Path, body: str) -> Path:
    (tmp_path / "pyproject.toml").write_text(body)
    return tmp_path


# -- declaration -----------------------------------------------------------


def test_a_declared_table_is_read_with_its_qualifiers_stripped(tmp_path: Path) -> None:
    """`localstack:s3,sqs` names the compose SERVICE `localstack`.

    The suffix says which AWS APIs the fragment enables, and it is not part of
    the service name -- `docker compose up localstack:s3,sqs` would fail with a
    message about an undefined service, several steps from the declaration that
    caused it.
    """
    declaration = load_declaration(_write(tmp_path, PYPROJECT_WITH))

    assert declaration.resources == ("postgres", "localstack")
    assert declaration.wants_postgres is True
    assert declaration.service == "worked-example"
    assert declaration.orm_metadata == "foundry_service_template.adapters.real.orm:Base"


def test_this_repositorys_own_declaration_is_the_one_the_kit_reads() -> None:
    """The production input, not a fixture. A gate that only ever reads its own
    fixture has never demonstrated it can read the real thing."""
    declaration = load_declaration(ROOT)

    # HAND-WRITTEN, and it MOVED when the declaration did. The three names are
    # the artifact's current content, not a count read back off it: an expectation
    # derived from `load_declaration` would hold for every possible pyproject,
    # including one whose table had been emptied by a typo.
    assert declaration.resources == ("postgres", "localstack", "redis")
    assert declaration.orm_metadata == "foundry_service_template.adapters.real.orm:Base"


def test_no_table_at_all_is_a_named_no_op_rather_than_a_refusal(tmp_path: Path) -> None:
    """A service with no database is a legitimate shape, and its real arm says so.

    Refusing here would force every service without persistence to carry an empty
    table to prove it meant it.
    """
    declaration = load_declaration(_write(tmp_path, '[project]\nname = "no-resources"\n'))

    assert declaration.resources == ()
    assert declaration.describe() == "none declared"


def test_a_malformed_resources_list_is_refused_rather_than_read_as_empty(tmp_path: Path) -> None:
    """The clause that matters most, because its failure mode is silent.

    A typo that read as "no resources" would turn the whole real arm off and
    report a green run over a suite that measured nothing.
    """
    body = '[project]\nname = "x"\n\n[tool.foundry.test-resources]\nresources = "postgres"\n'
    with pytest.raises(RealResourcesUnavailable) as excinfo:
        load_declaration(_write(tmp_path, body))

    assert "must be a list of strings" in str(excinfo.value)


def test_declaring_postgres_without_orm_metadata_is_refused(tmp_path: Path) -> None:
    """Its own clause, and its own fixture.

    Without this the kit would fall back to some default truncate set, and a
    TRUNCATE over the wrong tables isolates nothing while exiting 0.
    """
    body = '[project]\nname = "x"\n\n[tool.foundry.test-resources]\nresources = ["postgres"]\n'
    with pytest.raises(RealResourcesUnavailable) as excinfo:
        load_declaration(_write(tmp_path, body))

    assert "orm_metadata" in str(excinfo.value)


def test_a_missing_pyproject_names_the_path_it_looked_at(tmp_path: Path) -> None:
    with pytest.raises(RealResourcesUnavailable) as excinfo:
        load_declaration(tmp_path)

    assert str(tmp_path / "pyproject.toml") in str(excinfo.value)


# -- project name ----------------------------------------------------------


def test_two_checkouts_of_one_repository_get_different_project_names(tmp_path: Path) -> None:
    """THE SHARED-DATABASE FAILURE, as an assertion.

    Every run there shared one Docker daemon and one container name, so a sibling
    worktree's rebuild killed your database tests mid-suite. The hash is of the
    RESOLVED path, so two checkouts are two estates.
    """
    (tmp_path / "one").mkdir()
    (tmp_path / "two").mkdir()
    first = _write(tmp_path / "one", PYPROJECT_WITH)
    second = _write(tmp_path / "two", PYPROJECT_WITH)

    assert compose_project_name(first) != compose_project_name(second)
    # The positive control. Two names that differed because the function returns
    # a random string would satisfy the line above and share nothing else.
    assert compose_project_name(first) == compose_project_name(first)
    assert compose_project_name(first).startswith("shp-worked-example-")


def test_a_project_name_is_a_legal_compose_project_name(tmp_path: Path) -> None:
    """Compose rejects a project name outside `[a-z0-9][a-z0-9_-]*`.

    The service name comes from `[project] name`, which is free-form, so the
    sanitiser is load-bearing rather than cosmetic: a name with a dot or a capital
    would fail at `docker compose -p`, and the message is about compose rather
    than about the pyproject field that produced it.
    """
    # A name with NOTHING of this service in it, deliberately. `new_service.sh`
    # emits a renamed copy of this repository by rewriting the package name through
    # every tracked file, so a fixture spelled with the template's own name has one
    # half rewritten and the other not -- and the emitted service's suite then fails
    # on an assertion about a string nobody wrote. Measured: the assertion became
    # `shp-foundry-probe-service-` while the TOML body stayed
    # `Foundry.Service_Template`, because the two spellings differ.
    body = '[project]\nname = "Odd.Name_Here"\n'
    name = compose_project_name(_write(tmp_path, body))

    assert name.startswith("shp-odd-name-here-")
    assert all(character.islower() or character.isdigit() or character == "-" for character in name)


# -- worker database -------------------------------------------------------


@pytest.mark.parametrize(
    ("worker", "expected"),
    [("gw0", "test_gw0"), ("gw11", "test_gw11"), ("main", "test_main"), ("", "test_main")],
)
def test_the_worker_database_name_is_derived_from_the_worker_id(worker, expected) -> None:
    assert worker_database_name(worker) == expected


def test_a_worker_id_is_sanitised_before_it_reaches_ddl() -> None:
    """The name is interpolated into `CREATE DATABASE`, so it is checked.

    xdist only ever supplies `gwN`, which is exactly why this is worth asserting:
    a guarantee that holds because of a caller's current behaviour is one caller
    away from being false, and the consequence here is DDL injection.
    """
    assert (
        worker_database_name('gw0"; DROP DATABASE postgres; --')
        == "test_gw0_drop_database_postgres"
    )


# -- AWS credential guard --------------------------------------------------


def test_a_real_aws_profile_is_popped_and_fake_credentials_pinned() -> None:
    """The AWS-credential-leak guard.

    A developer with `AWS_PROFILE` exported runs the suite, and boto3 resolves
    THAT profile for any client whose endpoint override went missing -- so a test
    that meant to reach LocalStack reaches an account, and the first thing anyone
    notices is a bill or a mutated bucket.
    """
    environ = {"AWS_PROFILE": "production", "PATH": "/usr/bin"}

    changed = pin_fake_aws_credentials(environ)

    assert "AWS_PROFILE" not in environ, "a real profile must not survive into the run"
    assert environ["AWS_ACCESS_KEY_ID"] == "test"
    assert environ["AWS_SECRET_ACCESS_KEY"] == "test"
    assert environ["AWS_REGION"] == environ["AWS_DEFAULT_REGION"] == "us-east-1"
    assert changed["AWS_PROFILE"] == "<popped>"
    assert environ["PATH"] == "/usr/bin", "it must touch only the AWS keys"


def test_the_guard_reports_nothing_changed_when_there_was_nothing_to_change() -> None:
    """The negative control. A function that reported changes unconditionally
    would satisfy the arm above and tell a reader nothing."""
    environ = {
        "AWS_ACCESS_KEY_ID": "test",
        "AWS_SECRET_ACCESS_KEY": "test",
        "AWS_DEFAULT_REGION": "us-east-1",
        "AWS_REGION": "us-east-1",
    }

    assert pin_fake_aws_credentials(environ) == {}


# -- owned tables ----------------------------------------------------------


def test_the_truncate_set_is_derived_from_the_real_mapping() -> None:
    """Against the production metadata, compared to a hand-written literal.

    Deriving the expectation from `Base.metadata` would make this `x == x`: it
    would hold for an empty mapping, which is precisely the state a forgotten
    record-module import produces.
    """
    assert set(owned_tables("foundry_service_template.adapters.real.orm:Base")) == EXPECTED_TABLES


def test_a_mapping_with_no_tables_is_refused_rather_than_truncating_nothing() -> None:
    """A TRUNCATE over zero tables succeeds, isolates nothing and reads as a
    working harness.

    Its fixture is `tests/support/empty_metadata_fixture.py`, a real empty
    `MetaData` INSTANCE. The obvious shortcut, `sqlalchemy:MetaData`, resolves to
    the CLASS and reddens this arm through a different clause -- see the arm below,
    which owns that case. A control that is red for the wrong reason is
    indistinguishable from a working one.
    """
    with pytest.raises(RealResourcesUnavailable) as excinfo:
        owned_tables("tests.support.empty_metadata_fixture:EMPTY_METADATA")

    assert "maps no tables" in str(excinfo.value)


def test_pointing_orm_metadata_at_the_metadata_class_is_refused_by_name() -> None:
    """The near miss, with its own arm and its own message.

    `MetaData` the class has an unbound `sorted_tables` property, so iterating it
    raises `TypeError: 'property' object is not iterable` from inside the kit --
    a traceback that says nothing about the configuration line that caused it.
    """
    with pytest.raises(RealResourcesUnavailable) as excinfo:
        owned_tables("sqlalchemy:MetaData")

    message = str(excinfo.value)
    assert "not at the MetaData class" in message
    assert "maps no tables" not in message, (
        "this must be the SHAPE refusal, not the empty-mapping one; sharing a message "
        "would let either clause be deleted while both arms stayed red"
    )


def test_a_reference_that_is_not_module_colon_attribute_is_refused() -> None:
    with pytest.raises(RealResourcesUnavailable) as excinfo:
        owned_tables("foundry_service_template.adapters.real.orm")

    assert "module:Attribute" in str(excinfo.value)


def test_a_reference_naming_an_absent_attribute_is_refused() -> None:
    with pytest.raises(RealResourcesUnavailable) as excinfo:
        owned_tables("foundry_service_template.adapters.real.orm:NotThere")

    assert "NotThere" in str(excinfo.value)


# -- the controller -> worker channel --------------------------------------


def test_the_profile_survives_the_workerinput_round_trip() -> None:
    """An xdist worker is a separate PROCESS: everything it knows about the
    resources arrives as plain data through `workerinput`. A field added to
    `RealProfile` and forgotten in `as_dict` would leave workers running against a
    default -- silently, because every other field would still arrive."""
    profile = RealProfile(
        project="shp-x-0000abcd",
        admin_url="postgresql+psycopg://postgres:postgres@127.0.0.1:1/postgres",
        resources=("postgres",),
        orm_metadata_ref="pkg.orm:Base",
    )

    assert RealProfile.from_dict(profile.as_dict()) == profile


def test_every_profile_field_is_carried_by_as_dict() -> None:
    """The control for the arm above, which a round trip alone cannot give: a
    field missing from BOTH `as_dict` and `from_dict` round-trips perfectly."""
    import dataclasses

    declared = {field.name for field in dataclasses.fields(RealProfile)}

    assert declared == set(
        RealProfile(project="p", admin_url=None, resources=()).as_dict()
    )
