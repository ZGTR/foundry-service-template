"""Nothing this service deploys can be reached from the internet.

The template used to hand every new service a public front door: an `aws_lb` with
`internal = false` behind a security group open to `0.0.0.0/0` on the ECS path, and
an API Gateway HTTP API on the Lambda path. Both are gone. A service's only entry is
a rule on the estate's shared internal load balancer, and this file is what keeps a
copy of the old shape from coming back.

The IaC is PARSED, never searched. `deploy/iac/app/main.tf` legitimately names
`0.0.0.0/0` -- in the task's EGRESS block -- so a text search for the CIDR either
flags a correct file or has to guess which block a line belongs to. The parsed
document says which block it is.

THE EXPECTED VALUE OF EVERY CONTROL IS WRITTEN OUT BY HAND. A control whose
expectation is computed from its own fixture holds for any detector, including one
that sees nothing.

The Lambda rule is conditional on `custom.exposure`, the committed declaration that
must equal this service's row in the Foundry workspace's service directory. A
service whose row says `public` may declare an API Gateway event; the workspace's
own gate compares the two. The Terraform rules are unconditional: the release root
has one ingress whatever the exposure, and internet entry for a public operation
belongs to the backend's API Gateway.

That one ingress has one source: the shared internal load balancer's group. A service
that calls another private service carries the estate's caller group on its tasks, and
that is an egress-side membership. Nothing may admit the group, or anything else, into
the service. A `terraform test` reads the resources it names and cannot enumerate the
rest, so the refusal lives here, and it has two halves:

  the tasks carry exactly their own group and the optional caller group, so a second
  group is a finding whatever it admits;

  no ingress rule that may be on the service's group names another source, in any of
  the three ways Terraform spells a rule.

WHAT IS READ: every `*.tf` and `*.tf.json` file under `deploy/iac`, in every directory,
so a rule written in the resources root or in a local module is read like one in the
release root. WHAT IS NOT, AND IS REFUSED: a module call, because its resources may be
anywhere; and a `*.tofu` or `*.tofu.json` file, because OpenTofu may load one as
configuration, and loads `main.tofu` in place of `main.tf`, while Terraform ignores it.
A file one binary applies and the other never sees is not read here; it is a finding
that names the file.
A `*.tofutest.hcl` or `*.tofutest.json` file is also refused, because OpenTofu runs
it in place of the reviewed `.tftest.hcl` or `.tftest.json` file of the same name.
One shared test source avoids that second authority. A symlink under `deploy/iac`
is refused too: the walk does not follow it, and OpenTofu does, so what it points
to would be loaded and never read here.

WHAT IS NOT, AND IS NOT REFUSED: `.terraform` is skipped, because it holds what `init`
downloaded. Only three resource kinds are read as rules and only `aws_ecs_service` as
what launches the tasks: a rule written through a generic resource
(`aws_cloudformation_stack`, `aws_cloudcontrolapi_resource`) and a second launcher of
the same task definition (an EventBridge target, a scheduler) are out of this file's
sight. So is everything outside `deploy/iac` and everything that is not Terraform: a
rule added by hand or by another stack.
"""

from __future__ import annotations

import ipaddress
import json
import os
from collections.abc import Iterator
from pathlib import Path

import hcl2
import pytest
import yaml
from hcl2 import SerializationOptions

ROOT = Path(__file__).resolve().parents[2]
IAC = ROOT / "deploy" / "iac"
SERVERLESS = IAC / "serverless.yml"

EXPOSURES = ("private", "edge_iam", "public")
LOAD_BALANCERS = ("aws_lb", "aws_alb")
#: Every attribute a security-group rule names a source range with, across the
#: inline block, `aws_security_group_rule` and `aws_vpc_security_group_ingress_rule`.
CIDR_ATTRIBUTES = ("cidr_blocks", "ipv6_cidr_blocks", "cidr_ipv4", "cidr_ipv6")
#: Serverless events that create an API Gateway endpoint.
INTERNET_EVENTS = ("httpApi", "http", "websocket")
#: The two suffixes both Terraform and OpenTofu load a configuration file under. These
#: are read.
TERRAFORM_SUFFIXES = (".tf", ".tf.json")
#: The configuration and test suffixes only OpenTofu loads, preferring them over
#: the corresponding Terraform file of the same name.
#: These are refused, not read.
OPENTOFU_ONLY_SUFFIXES = (".tofu", ".tofu.json", ".tofutest.hcl", ".tofutest.json")
#: The Terraform name each OpenTofu-only suffix should be written under instead.
REPLACEMENTS = {".tofutest.hcl": ".tftest.hcl", ".tofutest.json": ".tftest.json"}
#: The group the tasks carry, and the one source that group admits.
SERVICE_GROUP = "aws_security_group.service"
ADMITTED_SOURCE = "${var.internal_alb_sg_id}"
#: What the tasks carry, as the parser returns the expression: their own group, and the
#: estate's caller group when one is named.
TASK_GROUPS = (
    "${concat([aws_security_group.service.id], "
    'var.estate_caller_sg_id == "" ? [] : [var.estate_caller_sg_id])}'
)
#: Every attribute an ingress rule names a source with, across the three rule kinds.
SOURCE_ATTRIBUTES = (
    *CIDR_ATTRIBUTES,
    "security_groups",
    "source_security_group_id",
    "referenced_security_group_id",
    "prefix_list_ids",
    "prefix_list_id",
    "self",
)

_PLAIN = SerializationOptions(with_comments=False, explicit_blocks=False, strip_string_quotes=True)


def _many(value: object) -> list:
    """A block as a list, however it is written. HCL repeats a block and the parser
    returns a list; the JSON syntax may write a single block as one bare object."""
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _document(name: str, text: str) -> dict:
    """One Terraform file, in whichever of the two syntaxes its name says."""
    if name.endswith(".tf.json"):
        return json.loads(text)
    return hcl2.loads(text, serialization_options=_PLAIN)


def _resources(name: str, text: str) -> Iterator[tuple[str, str, dict]]:
    for block in _many(_document(name, text).get("resource")):
        for kind, named in block.items():
            assert kind != "//", (
                f"{name}: cannot read // beside resource types; "
                "Terraform and OpenTofu refuse this level"
            )
            # The JSON syntax may write the level under a resource type as a list of
            # objects, too.
            for labels in _many(named):
                for label, bodies in labels.items():
                    assert label != "//", (
                        f"{name}: cannot read // beside resource labels; "
                        "Terraform and OpenTofu refuse this level"
                    )
                    for body in _many(bodies):
                        yield kind, label, body


def _module_calls(name: str, text: str) -> list[str]:
    return [label for block in _many(_document(name, text).get("module")) for label in block]


def _own_files(root: Path) -> list[Path]:
    """Every file under `root`, in every directory. `.terraform` is what `init`
    downloaded, not this service's own configuration."""
    return [
        path
        for path in sorted(root.rglob("*"))
        if path.is_file() and ".terraform" not in path.relative_to(root).parts
    ]


def _terraform_tree(root: Path) -> dict[str, str]:
    """Every Terraform file under `root`, in every directory and both syntaxes, as
    text by its path relative to `root`."""
    return {
        path.relative_to(root).as_posix(): path.read_text()
        for path in _own_files(root)
        if path.name.endswith(TERRAFORM_SUFFIXES)
    }


def opentofu_only_files(root: Path) -> list[str]:
    """Each file under `root` that OpenTofu may load and Terraform does
    not. Whatever it declares is applied by one binary and judged by nothing here, so
    the file is the finding, whatever is in it."""
    findings = []
    for path in _own_files(root):
        if path.name.endswith(OPENTOFU_ONLY_SUFFIXES):
            replacement = next(
                (tf for tofu, tf in REPLACEMENTS.items() if path.name.endswith(tofu)), ".tf"
            )
            findings.append(
                f"{path.relative_to(root).as_posix()}: OpenTofu may load this file as "
                "configuration or tests and this test does not read it; "
                f"write it as {replacement}"
            )
    return findings


def symlinks_under(root: Path) -> list[str]:
    """Each symlink under `root`, to a file or a directory. The walk above does not
    follow one, and OpenTofu does (a `tests` directory that is a symlink is run), so
    whatever it points to is loaded and judged by nothing here."""
    findings = []
    for directory, subdirectories, files in os.walk(root, followlinks=False):
        here = Path(directory)
        if ".terraform" in here.relative_to(root).parts:
            continue
        for name in sorted([*subdirectories, *files]):
            path = here / name
            if name != ".terraform" and path.is_symlink():
                findings.append(
                    f"{path.relative_to(root).as_posix()}: a symlink, which OpenTofu follows "
                    "and this test does not; copy what it points to into deploy/iac"
                )
    return sorted(findings)


def unread_by_this_test(root: Path) -> list[str]:
    """Everything under `root` this test refuses rather than reads: a file only OpenTofu
    loads, and a symlink. The repository test calls this, and so does its fixture test, so
    neither half can be dropped from one without the other going red."""
    return opentofu_only_files(root) + symlinks_under(root)


def _tree_resources(files: dict[str, str]) -> list[tuple[str, str, str, dict]]:
    return [
        (path, kind, label, body)
        for path, text in files.items()
        for kind, label, body in _resources(path, text)
    ]


def _ingress_rules(kind: str, body: dict) -> list[dict]:
    """The rules of `body` that admit traffic. A standalone rule counts as ingress
    unless it says `egress`, so one whose type is an expression is still read."""
    if kind == "aws_security_group":
        # `ingress` may be written as an attribute, and an attribute may hold an
        # expression instead of rules. That is read as one rule whose source is the
        # expression: a source this test cannot read, and so does not pass.
        return [
            rule if isinstance(rule, dict) else {"cidr_blocks": [rule]}
            for rule in _many(body.get("ingress"))
        ]
    if kind == "aws_security_group_rule" and body.get("type") != "egress":
        return [body]
    if kind == "aws_vpc_security_group_ingress_rule":
        return [body]
    return []


def _reaches_everyone(cidr: str) -> bool:
    """A range with prefix length 0, in any spelling. An expression is not a range
    this test can read, and what it cannot read it does not pass."""
    try:
        return ipaddress.ip_network(cidr, strict=False).prefixlen == 0
    except ValueError:
        return True


def internet_ingress_in_terraform(name: str, text: str) -> list[str]:
    findings = []
    for kind, label, body in _resources(name, text):
        where = f"{kind}.{label}"
        # The provider's default is internet-facing, so only a literal `true` passes.
        if kind in LOAD_BALANCERS and body.get("internal") is not True:
            findings.append(f"{where}: load balancer is not internal")
        for rule in _ingress_rules(kind, body):
            for attribute in CIDR_ATTRIBUTES:
                for cidr in _many(rule.get(attribute)):
                    if _reaches_everyone(str(cidr)):
                        findings.append(f"{where}: ingress from {cidr}")
        if kind == "aws_ecs_service":
            for network in _many(body.get("network_configuration")):
                if network.get("assign_public_ip", False) is not False:
                    findings.append(f"{where}: assign_public_ip is not false")
    return findings


def internet_ingress_in_tree(files: dict[str, str]) -> list[str]:
    return [
        f"{path}: {finding}"
        for path, text in files.items()
        for finding in internet_ingress_in_terraform(path, text)
    ]


def unexpected_groups_on_the_tasks(files: dict[str, str]) -> list[str]:
    """Each ECS service whose tasks carry anything but `TASK_GROUPS`.

    Whatever a second group admits reaches the tasks, so a second group is a finding
    without its rules being read. The comparison is with the whole expression: a
    list, a local or a module output in its place is a set of groups this test cannot
    count, and what it cannot count it does not pass.
    """
    findings = []
    for path, kind, label, body in _tree_resources(files):
        if kind != "aws_ecs_service":
            continue
        for network in _many(body.get("network_configuration")):
            groups = network.get("security_groups")
            if groups != TASK_GROUPS:
                findings.append(
                    f"{path}: {kind}.{label}: the tasks' security_groups is not the service's "
                    f"group plus the optional caller group: {groups}"
                )
    return findings


def _service_group_rules(files: dict[str, str]) -> list[tuple[str, dict]]:
    """Every ingress rule that may be on the service's group, as (where, rule).

    A rule is the service's when it is an inline block of that group, or a standalone
    rule whose `security_group_id` is not ANOTHER group the tree declares. So a rule
    that reaches the group through a data source, a variable or a local, which is how
    another root or a module has to name it, is not passed over: it is read as the
    service's.
    """
    resources = _tree_resources(files)
    other_groups = {
        f"${{{kind}.{label}.id}}"
        for _, kind, label, _ in resources
        if kind == "aws_security_group" and f"{kind}.{label}" != SERVICE_GROUP
    }
    rules = []
    for path, kind, label, body in resources:
        where = f"{path}: {kind}.{label}"
        if kind == "aws_security_group" and f"{kind}.{label}" != SERVICE_GROUP:
            continue
        if kind != "aws_security_group" and body.get("security_group_id") in other_groups:
            continue
        rules.extend((where, rule) for rule in _ingress_rules(kind, body))
        # `dynamic "ingress"` is parsed apart from the inline blocks, with its sources
        # behind an iterator. It is an ingress this test cannot read.
        dynamic = _many(body.get("dynamic")) if kind == "aws_security_group" else []
        if any("ingress" in block for block in dynamic):
            rules.append((where, {"security_groups": ["a dynamic block"]}))
    return rules


def other_ingress_into_the_service(files: dict[str, str]) -> list[str]:
    """Each source the service's group may admit that is not the internal load
    balancer's group, and each module call, whose resources are not in `files`."""
    findings = []
    for where, rule in _service_group_rules(files):
        for attribute in SOURCE_ATTRIBUTES:
            for source in _many(rule.get(attribute)):
                if source in (False, ADMITTED_SOURCE):
                    continue
                named = "itself" if attribute == "self" else source
                findings.append(f"{where}: ingress into the service's group from {named}")
    for path, text in files.items():
        for label in _module_calls(path, text):
            findings.append(
                f"{path}: module.{label}: a module call, whose resources this test does not follow"
            )
    return findings


def internet_ingress_in_serverless(text: str) -> list[str]:
    doc = yaml.safe_load(text) or {}
    exposure = (doc.get("custom") or {}).get("exposure")
    if exposure not in EXPOSURES:
        return [f"custom.exposure is {exposure!r}, not one of {', '.join(EXPOSURES)}"]
    if exposure == "public":
        return []
    functions = doc.get("functions")
    if not isinstance(functions, dict):
        return ["functions is not a mapping this test can read"]
    findings = []
    for name, spec in functions.items():
        events = spec.get("events", [])
        if not isinstance(events, list):
            # `events: ${file(...)}` resolves at deploy time, out of this test's sight.
            findings.append(f"functions.{name}: events is not a list this test can read")
            continue
        if spec.get("url"):
            findings.append(f"functions.{name}: function URL under exposure {exposure}")
        for event in events:
            declared = set(event) if isinstance(event, dict) else {event}
            for kind in INTERNET_EVENTS:
                if kind in declared:
                    findings.append(f"functions.{name}: {kind} event under exposure {exposure}")
    return findings


# --- the real files -------------------------------------------------------------


def _under_iac(findings: list[str]) -> list[str]:
    return [f"{IAC.relative_to(ROOT).as_posix()}/{finding}" for finding in findings]


def test_no_terraform_root_declares_internet_ingress() -> None:
    files = _terraform_tree(IAC)
    resources = _tree_resources(files)
    # VISIBILITY. "Found nothing" is this test's pass condition, so it first proves it
    # reached the resources an offender would be written in.
    kinds = [kind for _, kind, _, _ in resources]
    rules = [rule for _, kind, _, body in resources for rule in _ingress_rules(kind, body)]
    assert "aws_ecs_service" in kinds, f"no aws_ecs_service among {len(kinds)} parsed resource(s)"
    assert len(rules) >= 1, "parsed no ingress rule at all; the scan saw no security group"

    offenders = _under_iac(internet_ingress_in_tree(files))
    assert offenders == [], "\n".join(offenders)


def test_no_other_group_is_on_the_tasks() -> None:
    files = _terraform_tree(IAC)
    # VISIBILITY. "Nothing unexpected" is this test's pass condition, so it first proves
    # it read the one place the tasks' groups are written.
    read = [
        network
        for _, kind, _, body in _tree_resources(files)
        if kind == "aws_ecs_service"
        for network in _many(body.get("network_configuration"))
    ]
    assert len(read) == 1, f"read {len(read)} network configuration(s) on an ECS service, not 1"

    offenders = _under_iac(unexpected_groups_on_the_tasks(files))
    assert offenders == [], "\n".join(offenders)


def test_the_service_group_admits_only_the_internal_load_balancer() -> None:
    files = _terraform_tree(IAC)
    # VISIBILITY. "Found nothing else" is this test's pass condition, so it first proves
    # that it read a rule on the group the tasks carry, and more than one directory.
    on_the_group = [where for where, _ in _service_group_rules(files) if SERVICE_GROUP in where]
    assert len(on_the_group) >= 1, "read no ingress rule on the service's group"
    directories = {path.rpartition("/")[0] for path in files}
    assert len(directories) >= 2, f"read one directory of deploy/iac, not every root: {directories}"

    offenders = _under_iac(other_ingress_into_the_service(files))
    assert offenders == [], "\n".join(offenders)


def test_no_file_under_iac_is_one_only_opentofu_loads() -> None:
    # VISIBILITY. "Found none" is this test's pass condition, so it first proves the
    # same walk reached the template's own configuration in both roots.
    seen = {path.relative_to(IAC).as_posix() for path in _own_files(IAC)}
    assert {"app/main.tf", "resources/main.tf"} <= seen, f"the walk missed a root: {seen}"

    offenders = _under_iac(unread_by_this_test(IAC))
    assert offenders == [], "\n".join(offenders)


def test_the_lambda_target_declares_no_internet_ingress() -> None:
    text = SERVERLESS.read_text()
    functions = yaml.safe_load(text)["functions"]
    events = [event for spec in functions.values() for event in spec.get("events", [])]
    assert len(events) >= 1, "serverless.yml declares no event; the scan read nothing"
    offenders = internet_ingress_in_serverless(text)
    assert offenders == [], "\n".join(offenders)


# --- controls, both directions, one per clause ------------------------------------

_WORLD_OPEN_GROUP = """
resource "aws_security_group" "alb" {
  ingress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    %s
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}
"""

_ECS_SERVICE = """
resource "aws_ecs_service" "this" {
  network_configuration {
    subnets          = data.aws_subnets.private.ids
    assign_public_ip = %s
  }
}
"""

HCL_OFFENDERS = [
    pytest.param(
        'resource "aws_lb" "this" {\n  internal = false\n}\n',
        ["aws_lb.this: load balancer is not internal"],
        id="an internet-facing load balancer",
    ),
    pytest.param(
        'resource "aws_lb" "this" {\n  name = "x"\n}\n',
        ["aws_lb.this: load balancer is not internal"],
        id="a load balancer left on the provider default",
    ),
    pytest.param(
        _WORLD_OPEN_GROUP % 'cidr_blocks = ["0.0.0.0/0"]',
        ["aws_security_group.alb: ingress from 0.0.0.0/0"],
        id="ingress from 0.0.0.0/0, beside an egress block that is not a finding",
    ),
    pytest.param(
        _WORLD_OPEN_GROUP % 'ipv6_cidr_blocks = ["::/0"]',
        ["aws_security_group.alb: ingress from ::/0"],
        id="ingress from ::/0 and nothing else",
    ),
    pytest.param(
        'resource "aws_security_group_rule" "in" {\n  type        = "ingress"\n'
        '  cidr_blocks = ["0.0.0.0/0"]\n}\n',
        ["aws_security_group_rule.in: ingress from 0.0.0.0/0"],
        id="a standalone ingress rule",
    ),
    pytest.param(
        'resource "aws_security_group" "alb" {\n  ingress = local.rules\n}\n',
        ["aws_security_group.alb: ingress from ${local.rules}"],
        id="an ingress attribute that holds an expression",
    ),
    pytest.param(
        'resource "aws_vpc_security_group_ingress_rule" "in" {\n  cidr_ipv6 = "::/0"\n}\n',
        ["aws_vpc_security_group_ingress_rule.in: ingress from ::/0"],
        id="a standalone ipv6 ingress rule",
    ),
    pytest.param(
        _WORLD_OPEN_GROUP % "cidr_blocks = var.allowed_cidrs",
        ["aws_security_group.alb: ingress from ${var.allowed_cidrs}"],
        id="an ingress range this test cannot read",
    ),
    pytest.param(
        _ECS_SERVICE % "true",
        ["aws_ecs_service.this: assign_public_ip is not false"],
        id="a task with a public IP",
    ),
]

HCL_CONFORMING = [
    pytest.param(
        'resource "aws_security_group" "alb" {\n  ingress = [{\n'
        '    cidr_blocks = ["10.0.0.0/8"]\n  }]\n}\n',
        id="an ingress attribute that holds a rule from inside the VPC",
    ),
    pytest.param('resource "aws_lb" "this" {\n  internal = true\n}\n', id="an internal balancer"),
    pytest.param(
        _WORLD_OPEN_GROUP % "security_groups = [var.internal_alb_sg_id]",
        id="ingress from a security group, egress to anywhere",
    ),
    pytest.param(_WORLD_OPEN_GROUP % 'cidr_blocks = ["10.0.0.0/8"]', id="ingress from the VPC"),
    pytest.param(
        'resource "aws_security_group_rule" "out" {\n  type        = "egress"\n'
        '  cidr_blocks = ["0.0.0.0/0"]\n}\n',
        id="a standalone egress rule to anywhere",
    ),
    pytest.param(_ECS_SERVICE % "false", id="a task with no public IP"),
]


@pytest.mark.parametrize("snippet, expected", HCL_OFFENDERS)
def test_each_internet_ingress_shape_in_terraform_is_found(
    snippet: str, expected: list[str]
) -> None:
    assert internet_ingress_in_terraform("main.tf", snippet) == expected


@pytest.mark.parametrize("snippet", HCL_CONFORMING)
def test_conforming_terraform_is_left_alone(snippet: str) -> None:
    assert internet_ingress_in_terraform("main.tf", snippet) == []


def _on_disk(tmp_path: Path, files: dict[str, str]) -> Path:
    for name, text in files.items():
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    return tmp_path


def _written(tmp_path: Path, files: dict[str, str]) -> dict[str, str]:
    """`files` on disk under `tmp_path`, read back by the reader the real-file tests
    use. A fixture is then found, or missed, the way the template's own files are."""
    return _terraform_tree(_on_disk(tmp_path, files))


def _json_resource(kind: str, label: str, body: dict) -> str:
    return json.dumps({"resource": {kind: {label: body}}})


_WORLD_OPEN_RULE = (
    'resource "aws_security_group_rule" "in" {\n  type        = "ingress"\n'
    '  cidr_blocks = ["0.0.0.0/0"]\n}\n'
)

INTERNET_TREES = [
    pytest.param(
        {"app/modules/edge/main.tf": 'resource "aws_lb" "this" {\n  internal = false\n}\n'},
        ["app/modules/edge/main.tf: aws_lb.this: load balancer is not internal"],
        id="a public load balancer in a directory below a root",
    ),
    pytest.param(
        {
            "app/extra.tf.json": _json_resource(
                "aws_security_group_rule", "in", {"type": "ingress", "cidr_blocks": ["0.0.0.0/0"]}
            )
        },
        ["app/extra.tf.json: aws_security_group_rule.in: ingress from 0.0.0.0/0"],
        id="a standalone rule from 0.0.0.0/0 in a .tf.json file",
    ),
    pytest.param(
        {
            "app/main.tf.json": _json_resource(
                "aws_security_group", "alb", {"ingress": {"ipv6_cidr_blocks": ["::/0"]}}
            )
        },
        ["app/main.tf.json: aws_security_group.alb: ingress from ::/0"],
        id="an inline rule from ::/0 written as one JSON object",
    ),
    pytest.param(
        {
            "app/extra.tf.json": _json_resource(
                "aws_security_group_rule", "out", {"type": "egress", "cidr_blocks": ["0.0.0.0/0"]}
            )
        },
        [],
        id="a standalone egress rule to anywhere in a .tf.json file",
    ),
    pytest.param(
        {"app/.terraform/modules/edge/main.tf": _WORLD_OPEN_RULE},
        [],
        id="what init downloaded is not this service's configuration",
    ),
    pytest.param(
        {
            "app/extra.tf.json": json.dumps(
                {
                    "resource": {
                        "aws_security_group_rule": [
                            {"in": {"type": "ingress", "cidr_blocks": ["0.0.0.0/0"]}}
                        ]
                    }
                }
            )
        },
        ["app/extra.tf.json: aws_security_group_rule.in: ingress from 0.0.0.0/0"],
        id="a .tf.json file whose level under the resource type is a list",
    ),
]


@pytest.mark.parametrize("files, expected", INTERNET_TREES)
def test_internet_ingress_is_found_in_every_directory_in_both_syntaxes(
    tmp_path: Path, files: dict[str, str], expected: list[str]
) -> None:
    assert internet_ingress_in_tree(_written(tmp_path, files)) == expected


_NOT_READ = (
    ": OpenTofu may load this file as configuration or tests "
    "and this test does not read it; write it as .tf"
)
_NOT_TEST_READ = (
    ": OpenTofu may load this file as configuration or tests "
    "and this test does not read it; write it as .tftest.hcl"
)
_NOT_JSON_TEST_READ = (
    ": OpenTofu may load this file as configuration or tests "
    "and this test does not read it; write it as .tftest.json"
)

# What is in the file does not matter: a rule that would be a finding in a `.tf` file,
# and one that would not, are refused alike.
OPENTOFU_ONLY_TREES = [
    pytest.param(
        {
            "app/tests/estate_caller_sg.tftest.hcl": "",
            "app/tests/estate_caller_sg.tofutest.hcl": "",
        },
        ["app/tests/estate_caller_sg.tofutest.hcl" + _NOT_TEST_READ],
        id="an OpenTofu test shadows the reviewed Terraform test",
    ),
    pytest.param(
        {"app/tests/estate_caller_sg.tftest.hcl": ""},
        [],
        id="the reviewed test without a shadow",
    ),
    pytest.param(
        {
            "app/tests/estate_caller_sg.tftest.json": '{"run": {"reviewed": {}}}\n',
            "app/tests/estate_caller_sg.tofutest.json": '{"run": {"shadow": {}}}\n',
        },
        ["app/tests/estate_caller_sg.tofutest.json" + _NOT_JSON_TEST_READ],
        id="an OpenTofu JSON test shadows the reviewed Terraform JSON test",
    ),
    pytest.param(
        {"app/tests/estate_caller_sg.tftest.json": '{"run": {"reviewed": {}}}\n'},
        [],
        id="the reviewed JSON test without a shadow",
    ),
    pytest.param(
        {"app/extra.tofu": _WORLD_OPEN_RULE},
        ["app/extra.tofu" + _NOT_READ],
        id="a rule in a .tofu file",
    ),
    pytest.param(
        {
            "app/extra.tofu.json": _json_resource(
                "aws_security_group_rule", "in", {"type": "ingress", "cidr_blocks": ["0.0.0.0/0"]}
            )
        },
        ["app/extra.tofu.json" + _NOT_READ],
        id="a rule in a .tofu.json file",
    ),
    pytest.param(
        {"resources/main.tf": "", "resources/main.tofu": 'resource "aws_lb" "this" {}\n'},
        ["resources/main.tofu" + _NOT_READ],
        id="main.tofu, which OpenTofu loads in place of main.tf",
    ),
    pytest.param(
        {"app/modules/edge/rules.tofu": ""},
        ["app/modules/edge/rules.tofu" + _NOT_READ],
        id="an empty .tofu file in a directory below a root",
    ),
    pytest.param(
        {"app/extra.tf": _WORLD_OPEN_RULE},
        [],
        id="the same rule in a .tf file, which the other tests read",
    ),
    pytest.param(
        {
            "app/extra.tf.json": _json_resource(
                "aws_security_group_rule", "in", {"type": "ingress", "cidr_blocks": ["0.0.0.0/0"]}
            )
        },
        [],
        id="the same rule in a .tf.json file, which the other tests read",
    ),
    pytest.param(
        {"app/.terraform/modules/edge/main.tofu": _WORLD_OPEN_RULE},
        [],
        id="a .tofu file init downloaded",
    ),
    pytest.param(
        {"app/notes.tofu/README.md": "", "app/tofu.tf": "", "app/main.tofu.md": ""},
        [],
        id="a directory, a stem and a middle part that are only named tofu",
    ),
]


@pytest.mark.parametrize("files, expected", OPENTOFU_ONLY_TREES)
def test_a_file_only_opentofu_loads_is_a_finding_whatever_it_holds(
    tmp_path: Path, files: dict[str, str], expected: list[str]
) -> None:
    assert unread_by_this_test(_on_disk(tmp_path, files)) == expected


def _linked(tmp_path: Path, links: dict[str, str]) -> Path:
    """`links` as symlinks under `tmp_path / "iac"`, each pointing at a real file or
    directory outside it, as a checkout's link would."""
    outside = tmp_path / "outside"
    _on_disk(outside, {"tests/t.tofutest.hcl": "", "world.tf": _WORLD_OPEN_RULE})
    root = tmp_path / "iac"
    _on_disk(root, {"app/main.tf": ""})
    for name, target in links.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).symlink_to(outside / target)
    return root


SYMLINK_TREES = [
    pytest.param({"app/tests": "tests"}, ["app/tests"], id="a symlinked tests directory"),
    pytest.param({"app/world.tf": "world.tf"}, ["app/world.tf"], id="a symlinked .tf file"),
    pytest.param(
        {"app/.terraform/modules/edge": "tests"}, [], id="a symlink init made under .terraform"
    ),
    pytest.param({}, [], id="no symlink"),
]


@pytest.mark.parametrize("links, expected", SYMLINK_TREES)
def test_a_symlink_under_the_iac_directory_is_a_finding(
    tmp_path: Path, links: dict[str, str], expected: list[str]
) -> None:
    findings = unread_by_this_test(_linked(tmp_path, links))
    assert [finding.split(":")[0] for finding in findings] == expected, findings


def test_the_walk_is_relative_to_its_root_under_a_terraform_parent(tmp_path: Path) -> None:
    root = tmp_path / ".terraform" / "checkout" / "deploy" / "iac"
    _on_disk(root, {"app/main.tf": "", "app/.terraform/provider.tf": ""})
    assert [path.relative_to(root).as_posix() for path in _own_files(root)] == ["app/main.tf"]


@pytest.mark.parametrize("level", ["types", "labels"])
def test_json_comment_keys_at_unreadable_resource_levels_are_named(level: str) -> None:
    resource = {"//": "comment"} if level == "types" else {"aws_security_group": {"//": "comment"}}
    text = json.dumps({"resource": resource})
    with pytest.raises(AssertionError, match=f"cannot read // beside resource {level}"):
        _tree_resources({"app/comment.tf.json": text})


def test_a_json_comment_inside_a_resource_body_is_read() -> None:
    body = {"//": "valid comment", "ingress": {"cidr_blocks": ["0.0.0.0/0"]}}
    assert internet_ingress_in_terraform(
        "ok.tf.json", _json_resource("aws_security_group", "service", body)
    ) == ["aws_security_group.service: ingress from 0.0.0.0/0"]


def test_the_release_exports_its_service_group_id() -> None:
    doc = _document("outputs.tf", (IAC / "app" / "outputs.tf").read_text())
    outputs = {name: body for block in _many(doc.get("output")) for name, body in block.items()}
    assert outputs.get("service_security_group_id", {}).get("value") == (
        "${aws_security_group.service.id}"
    ), "the release must export its own service group's id"


_TASKS = """
resource "aws_ecs_service" "this" {
  network_configuration {
    subnets          = data.aws_subnets.private.ids
    security_groups  = %s
    assign_public_ip = false
  }
}
"""

_TEMPLATE_TASK_GROUPS = """concat(
      [aws_security_group.service.id],
      var.estate_caller_sg_id == "" ? [] : [var.estate_caller_sg_id],
    )"""

_NOT_THE_TWO = (
    ": aws_ecs_service.this: the tasks' security_groups is not the service's group plus "
    "the optional caller group: "
)

TASK_GROUP_OFFENDERS = [
    pytest.param(
        {"app/main.tf": _TASKS % "[aws_security_group.service.id, aws_security_group.aux.id]"},
        [
            "app/main.tf"
            + _NOT_THE_TWO
            + "['${aws_security_group.service.id}', '${aws_security_group.aux.id}']"
        ],
        id="a second group beside the service's own",
    ),
    pytest.param(
        {
            "app/main.tf": _TASKS
            % 'concat([aws_security_group.service.id], [aws_security_group.aux.id], var.estate_caller_sg_id == "" ? [] : [var.estate_caller_sg_id])'  # noqa: E501
        },
        [
            "app/main.tf" + _NOT_THE_TWO + "${concat([aws_security_group.service.id], "
            '[aws_security_group.aux.id], var.estate_caller_sg_id == "" ? [] : '
            "[var.estate_caller_sg_id])}"
        ],
        id="a second group added to the template's own expression",
    ),
    pytest.param(
        {"app/main.tf": _TASKS % "local.task_groups"},
        ["app/main.tf" + _NOT_THE_TWO + "${local.task_groups}"],
        id="the groups behind a local",
    ),
    pytest.param(
        {
            "app/main.tf.json": _json_resource(
                "aws_ecs_service",
                "this",
                {"network_configuration": {"security_groups": ["${aws_security_group.aux.id}"]}},
            )
        },
        ["app/main.tf.json" + _NOT_THE_TWO + "['${aws_security_group.aux.id}']"],
        id="another group, the network configuration written as one JSON object",
    ),
]

TASK_GROUP_CONFORMING = [
    pytest.param(
        {"app/main.tf": _TASKS % _TEMPLATE_TASK_GROUPS}, id="the template's own expression"
    ),
    pytest.param(
        {
            "app/main.tf.json": _json_resource(
                "aws_ecs_service",
                "this",
                {"network_configuration": {"security_groups": TASK_GROUPS}},
            )
        },
        id="the template's expression, written as one JSON object",
    ),
]


@pytest.mark.parametrize("files, expected", TASK_GROUP_OFFENDERS)
def test_each_other_set_of_groups_on_the_tasks_is_found(
    tmp_path: Path, files: dict[str, str], expected: list[str]
) -> None:
    assert unexpected_groups_on_the_tasks(_written(tmp_path, files)) == expected


@pytest.mark.parametrize("files", TASK_GROUP_CONFORMING)
def test_the_tasks_own_group_with_the_caller_group_is_left_alone(
    tmp_path: Path, files: dict[str, str]
) -> None:
    assert unexpected_groups_on_the_tasks(_written(tmp_path, files)) == []


_SERVICE_GROUP = """
resource "aws_security_group" "service" {
  ingress {
    from_port = 8080
    to_port   = 8080
    protocol  = "tcp"
    %s
  }
}
"""

_STANDALONE_RULE = """
resource "aws_security_group" "service" {}
resource "aws_security_group" "cache" {}
resource "%s" "in" {
  %s
}
"""

_INTO = ": ingress into the service's group from "

SERVICE_GROUP_OFFENDERS = [
    pytest.param(
        _SERVICE_GROUP % "security_groups = [var.internal_alb_sg_id, var.estate_caller_sg_id]",
        ["app/main.tf: aws_security_group.service" + _INTO + "${var.estate_caller_sg_id}"],
        id="the caller group beside the load balancer's, inline",
    ),
    pytest.param(
        _STANDALONE_RULE
        % (
            "aws_security_group_rule",
            'type                     = "ingress"\n'
            "  security_group_id        = aws_security_group.service.id\n"
            "  source_security_group_id = var.estate_caller_sg_id",
        ),
        ["app/main.tf: aws_security_group_rule.in" + _INTO + "${var.estate_caller_sg_id}"],
        id="the caller group, as a standalone rule",
    ),
    pytest.param(
        _STANDALONE_RULE
        % (
            "aws_vpc_security_group_ingress_rule",
            "security_group_id            = aws_security_group.service.id\n"
            "  referenced_security_group_id = var.estate_caller_sg_id",
        ),
        [
            "app/main.tf: aws_vpc_security_group_ingress_rule.in"
            + _INTO
            + "${var.estate_caller_sg_id}"
        ],
        id="the caller group, as a standalone VPC ingress rule",
    ),
    pytest.param(
        _SERVICE_GROUP % 'cidr_blocks = ["10.0.0.0/8"]',
        ["app/main.tf: aws_security_group.service" + _INTO + "10.0.0.0/8"],
        id="the whole VPC",
    ),
    pytest.param(
        _SERVICE_GROUP % "self = true",
        ["app/main.tf: aws_security_group.service" + _INTO + "itself"],
        id="the group's own members",
    ),
    pytest.param(
        _STANDALONE_RULE
        % (
            "aws_vpc_security_group_ingress_rule",
            "security_group_id = aws_security_group.service.id\n"
            "  prefix_list_id    = data.aws_ec2_managed_prefix_list.peers.id",
        ),
        [
            "app/main.tf: aws_vpc_security_group_ingress_rule.in"
            + _INTO
            + "${data.aws_ec2_managed_prefix_list.peers.id}"
        ],
        id="a prefix list",
    ),
    pytest.param(
        _STANDALONE_RULE
        % (
            "aws_security_group_rule",
            'type                     = "ingress"\n'
            "  security_group_id        = local.target_group\n"
            "  source_security_group_id = var.estate_caller_sg_id",
        ),
        ["app/main.tf: aws_security_group_rule.in" + _INTO + "${var.estate_caller_sg_id}"],
        id="a standalone rule whose target this test cannot read",
    ),
    pytest.param(
        'resource "aws_security_group" "service" {\n  dynamic "ingress" {\n'
        "    for_each = var.callers\n    content {\n      security_groups = [ingress.value]\n"
        "    }\n  }\n}\n",
        ["app/main.tf: aws_security_group.service" + _INTO + "a dynamic block"],
        id="a dynamic ingress block",
    ),
    pytest.param(
        'resource "aws_security_group" "service" {\n  ingress = local.rules\n}\n',
        ["app/main.tf: aws_security_group.service" + _INTO + "${local.rules}"],
        id="an ingress attribute that holds an expression",
    ),
]

SERVICE_GROUP_CONFORMING = [
    pytest.param(
        'resource "aws_security_group" "service" {\n  ingress = [{\n'
        "    security_groups = [var.internal_alb_sg_id]\n  }]\n}\n",
        id="an ingress attribute that holds the template's own rule",
    ),
    pytest.param(
        _SERVICE_GROUP % "security_groups = [var.internal_alb_sg_id]",
        id="the template's own shape",
    ),
    pytest.param(
        _STANDALONE_RULE
        % (
            "aws_security_group_rule",
            'type                     = "ingress"\n'
            "  security_group_id        = aws_security_group.service.id\n"
            "  source_security_group_id = var.internal_alb_sg_id\n"
            "  self                     = false",
        ),
        id="the load balancer's group, as a standalone rule",
    ),
    pytest.param(
        _STANDALONE_RULE
        % (
            "aws_vpc_security_group_ingress_rule",
            "security_group_id            = aws_security_group.service.id\n"
            "  referenced_security_group_id = var.internal_alb_sg_id",
        ),
        id="the load balancer's group, as a standalone VPC ingress rule",
    ),
    pytest.param(
        _STANDALONE_RULE
        % (
            "aws_security_group_rule",
            'type              = "egress"\n'
            "  security_group_id = aws_security_group.service.id\n"
            '  cidr_blocks       = ["0.0.0.0/0"]',
        ),
        id="a standalone egress rule on the service's group",
    ),
    pytest.param(
        _STANDALONE_RULE
        % (
            "aws_security_group_rule",
            'type                     = "ingress"\n'
            "  security_group_id        = aws_security_group.cache.id\n"
            "  source_security_group_id = aws_security_group.service.id",
        ),
        id="a standalone rule on another group declared in the root",
    ),
    pytest.param(
        'resource "aws_security_group" "cache" {\n  ingress {\n'
        "    security_groups = [aws_security_group.service.id]\n  }\n}\n",
        id="another group's inline rule",
    ),
]


@pytest.mark.parametrize("snippet, expected", SERVICE_GROUP_OFFENDERS)
def test_each_other_source_into_the_service_group_is_found(
    tmp_path: Path, snippet: str, expected: list[str]
) -> None:
    files = _written(tmp_path, {"app/main.tf": snippet})
    assert other_ingress_into_the_service(files) == expected


@pytest.mark.parametrize("snippet", SERVICE_GROUP_CONFORMING)
def test_the_load_balancer_as_the_only_source_is_left_alone(tmp_path: Path, snippet: str) -> None:
    files = _written(tmp_path, {"app/main.tf": snippet})
    assert other_ingress_into_the_service(files) == []


# One fixture per place a rule can be written outside the release root's own `*.tf`,
# each beside the template's own group, and each with a twin that must stay quiet.
_OWN_GROUP = {"app/main.tf": _SERVICE_GROUP % "security_groups = [var.internal_alb_sg_id]"}

_RULE_FROM_THE_CALLER_GROUP = """
resource "aws_security_group_rule" "in" {
  type                     = "ingress"
  security_group_id        = %s
  source_security_group_id = var.estate_caller_sg_id
}
"""

_THROUGH_A_DATA_SOURCE = (
    'data "aws_security_group" "service" {\n  tags = { "foundry:exposure" = "private" }\n}\n'
    + _RULE_FROM_THE_CALLER_GROUP % "data.aws_security_group.service.id"
)

_ON_A_GROUP_OF_ITS_OWN = (
    'resource "aws_security_group" "cache" {}\n'
    + _RULE_FROM_THE_CALLER_GROUP % "aws_security_group.cache.id"
)

_MODULE_CALL = 'module "callers" {\n  source = "git::https://example.invalid/callers.git"\n}\n'

_ONLY_NAMED_MODULE = 'variable "module" {}\nresource "aws_ssm_parameter" "module" {}\n'

OTHER_PLACE_OFFENDERS = [
    pytest.param(
        {**_OWN_GROUP, "resources/main.tf": _THROUGH_A_DATA_SOURCE},
        ["resources/main.tf: aws_security_group_rule.in" + _INTO + "${var.estate_caller_sg_id}"],
        id="a rule in the resources root, through a data source",
    ),
    pytest.param(
        {
            **_OWN_GROUP,
            "app/modules/callers/main.tf": _RULE_FROM_THE_CALLER_GROUP % "var.group_id",
        },
        [
            "app/modules/callers/main.tf: aws_security_group_rule.in"
            + _INTO
            + "${var.estate_caller_sg_id}"
        ],
        id="a rule in a local module, through a variable",
    ),
    pytest.param(
        {
            **_OWN_GROUP,
            "app/extra.tf.json": _json_resource(
                "aws_security_group_rule",
                "in",
                {
                    "type": "ingress",
                    "security_group_id": "${aws_security_group.service.id}",
                    "source_security_group_id": "sg-0c0ffee0c0ffee0c0",
                },
            ),
        },
        ["app/extra.tf.json: aws_security_group_rule.in" + _INTO + "sg-0c0ffee0c0ffee0c0"],
        id="a rule in a .tf.json file",
    ),
    pytest.param(
        {
            "app/main.tf.json": _json_resource(
                "aws_security_group",
                "service",
                {
                    "ingress": {
                        "security_groups": [
                            "${var.internal_alb_sg_id}",
                            "${var.estate_caller_sg_id}",
                        ]
                    }
                },
            )
        },
        ["app/main.tf.json: aws_security_group.service" + _INTO + "${var.estate_caller_sg_id}"],
        id="an inline rule written as one JSON object",
    ),
    pytest.param(
        {"app/main.tf": _OWN_GROUP["app/main.tf"] + _MODULE_CALL},
        ["app/main.tf: module.callers: a module call, whose resources this test does not follow"],
        id="a module call",
    ),
]

OTHER_PLACE_CONFORMING = [
    pytest.param(
        {**_OWN_GROUP, "resources/main.tf": _ON_A_GROUP_OF_ITS_OWN},
        id="a rule in the resources root, on a group that root declares",
    ),
    pytest.param(
        {**_OWN_GROUP, "app/modules/cache/main.tf": _ON_A_GROUP_OF_ITS_OWN},
        id="a rule in a local module, on a group the module declares",
    ),
    pytest.param(
        {
            **_OWN_GROUP,
            "app/extra.tf.json": _json_resource(
                "aws_security_group_rule",
                "in",
                {
                    "type": "ingress",
                    "security_group_id": "${aws_security_group.service.id}",
                    "source_security_group_id": "${var.internal_alb_sg_id}",
                },
            ),
        },
        id="the load balancer's group, in a .tf.json file",
    ),
    pytest.param(
        {
            "app/main.tf.json": _json_resource(
                "aws_security_group",
                "service",
                {"ingress": {"security_groups": ["${var.internal_alb_sg_id}"]}},
            )
        },
        id="the template's own rule written as one JSON object",
    ),
    pytest.param(
        {"app/main.tf": _OWN_GROUP["app/main.tf"] + _ONLY_NAMED_MODULE},
        id="a variable and a resource that are only named module",
    ),
    pytest.param(
        {
            **_OWN_GROUP,
            "app/.terraform/modules/callers/main.tf": _RULE_FROM_THE_CALLER_GROUP % "var.group_id",
        },
        id="what init downloaded is not this service's configuration",
    ),
]


@pytest.mark.parametrize("files, expected", OTHER_PLACE_OFFENDERS)
def test_a_rule_written_anywhere_under_the_iac_directory_is_found(
    tmp_path: Path, files: dict[str, str], expected: list[str]
) -> None:
    assert other_ingress_into_the_service(_written(tmp_path, files)) == expected


@pytest.mark.parametrize("files", OTHER_PLACE_CONFORMING)
def test_a_rule_on_another_group_is_left_alone_wherever_it_is_written(
    tmp_path: Path, files: dict[str, str]
) -> None:
    assert other_ingress_into_the_service(_written(tmp_path, files)) == []


_SERVERLESS = """
custom:
  exposure: %s
functions:
  api:
    %s
"""

_ALB_EVENTS = """events:
      - alb:
          listenerArn: ${env:INTERNAL_LISTENER_ARN}
          priority: 100
          conditions:
            host: svc.example.internal
"""

SERVERLESS_CASES = [
    pytest.param(
        "private",
        'events:\n      - httpApi: "*"\n',
        ["functions.api: httpApi event under exposure private"],
        id="an httpApi event under exposure private",
    ),
    pytest.param(
        "edge_iam",
        "events:\n      - http: GET /widgets\n",
        ["functions.api: http event under exposure edge_iam"],
        id="an http event under exposure edge_iam",
    ),
    pytest.param(
        "private",
        "events:\n      - websocket: $connect\n",
        ["functions.api: websocket event under exposure private"],
        id="a websocket event under exposure private",
    ),
    pytest.param(
        "private",
        "url: true\n    events: []\n",
        ["functions.api: function URL under exposure private"],
        id="a function URL under exposure private",
    ),
    pytest.param(
        "private",
        "events: ${file(./events.yml)}\n",
        ["functions.api: events is not a list this test can read"],
        id="events hidden behind a variable",
    ),
    pytest.param(
        "internal",
        _ALB_EVENTS,
        ["custom.exposure is 'internal', not one of private, edge_iam, public"],
        id="an exposure that is not one of the three",
    ),
    pytest.param("private", _ALB_EVENTS, [], id="the template's own shape"),
    pytest.param(
        "public", 'events:\n      - httpApi: "*"\n', [], id="an httpApi event under exposure public"
    ),
]


@pytest.mark.parametrize("exposure, function_body, expected", SERVERLESS_CASES)
def test_an_api_gateway_entry_needs_exposure_public(
    exposure: str, function_body: str, expected: list[str]
) -> None:
    assert internet_ingress_in_serverless(_SERVERLESS % (exposure, function_body)) == expected


def test_a_serverless_file_with_no_declared_exposure_is_a_finding() -> None:
    assert internet_ingress_in_serverless("functions: {}\n") == [
        "custom.exposure is None, not one of private, edge_iam, public"
    ]
