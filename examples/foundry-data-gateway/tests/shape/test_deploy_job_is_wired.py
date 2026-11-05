"""The deploy job's smoke and rollback steps DO what their names say, in the right order.

Both steps used to carry an `echo` as their entire body, so the Actions UI showed
a green "Smoke (one success, one expected denial)" and a "Rollback on red smoke"
beside claims nothing tested. The scripts they now call are proved in
`test_smoke_script.py` and `test_lambda_release_script.py`; this file proves the
WORKFLOW reaches them with the right inputs, because a correct script that a step
never calls, or calls with an output nobody set, is the same inert step again.

Read as parsed YAML, never as text: step names and comments are prose ABOUT the
behaviour, and only the `run`, `if`, `env` and `id` keys are the behaviour.
"""

from __future__ import annotations

import datetime
import importlib.util
import json
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"

#: A body every line of which is one of these does nothing a step name could assert.
INERT_LINE = re.compile(r"^\s*(echo\b.*|true|:)?\s*$")


def _deploy_job() -> dict:
    return yaml.safe_load(WORKFLOW.read_text())["jobs"]["deploy-dev"]


def _deploy_steps() -> list[dict]:
    return _deploy_job()["steps"]


def _index(steps: list[dict], needle: str) -> int:
    matches = [i for i, step in enumerate(steps) if needle in (step.get("run") or "")]
    assert len(matches) == 1, f"expected exactly one deploy-dev step running {needle!r}: {matches}"
    return matches[0]


def test_no_deploy_step_has_an_inert_body() -> None:
    steps = _deploy_steps()
    runs = [s for s in steps if "run" in s]
    assert len(runs) >= 5, f"parsed only {len(runs)} run step(s); the matcher is broken"
    inert = [
        s.get("name") for s in runs if all(INERT_LINE.match(ln) for ln in s["run"].splitlines())
    ]
    assert inert == [], f"deploy-dev step(s) whose whole body is echo/true: {inert}"


def test_the_rollback_target_is_recorded_before_anything_is_pushed_or_deployed() -> None:
    steps = _deploy_steps()
    record = _index(steps, "lambda_release.sh previous-image")
    assert steps[record].get("id") == "previous"
    assert '>> "$GITHUB_OUTPUT"' in steps[record]["run"]
    assert record < _index(steps, "docker push"), "record the target before pushing"
    deploy = next(i for i, s in enumerate(steps) if s.get("id") == "deploy")
    assert "serverless@3 deploy" in steps[deploy]["run"]
    assert record < deploy


def test_the_smoke_reads_the_url_the_deploy_exposed() -> None:
    steps = _deploy_steps()
    deploy = next(i for i, s in enumerate(steps) if s.get("id") == "deploy")
    url = _index(steps, "lambda_release.sh api-url")
    smoke = _index(steps, "service-kit/ci/smoke.py")
    assert deploy < url < smoke
    assert steps[url].get("id") == "url"
    assert '>> "$GITHUB_OUTPUT"' in steps[url]["run"]
    assert steps[smoke]["env"]["API_URL"] == "${{ steps.url.outputs.api_url }}"
    assert '"$API_URL"' in steps[smoke]["run"]


def test_the_smoke_is_handed_the_function_the_rollback_would_redeploy() -> None:
    """One workflow serves both transports: the step always names the function, and the
    service's own `deploy/smoke.json` decides whether the name is used."""
    steps = _deploy_steps()
    smoke = steps[_index(steps, "service-kit/ci/smoke.py")]
    rollback = steps[_index(steps, "lambda_release.sh rollback")]
    assert smoke["run"].split() == [
        "python3",
        "service-kit/ci/smoke.py",
        '"$API_URL"',
        "--function-name",
        '"$FUNCTION_NAME"',
    ]
    assert '"$FUNCTION_NAME"' in rollback["run"]


def test_the_function_is_named_once_for_the_three_steps_that_read_it() -> None:
    """The rollback target is read from it, the smoke asks it and the rollback
    redeploys it. Written per step, one copy could name another function and the
    other two would not notice."""
    job = _deploy_job()
    named = (job.get("env") or {}).get("FUNCTION_NAME", "")
    assert named.endswith("-${{ inputs.target_environment }}-api"), f"the job names {named!r}"
    own = [s.get("name") for s in job["steps"] if "FUNCTION_NAME" in (s.get("env") or {})]
    assert own == [], f"step(s) that set a FUNCTION_NAME of their own: {own}"
    # In a step's shell too: `FUNCTION_NAME=other; ...`, or a line written to $GITHUB_ENV.
    shell = [
        s.get("id") or s.get("name")
        for s in job["steps"]
        if "FUNCTION_NAME=" in (s.get("run") or "")
    ]
    assert shell == [], f"step(s) whose shell sets a FUNCTION_NAME: {shell}"
    readers = [s for s in job["steps"] if '"$FUNCTION_NAME"' in (s.get("run") or "")]
    assert [s.get("id") or s["name"] for s in readers] == [
        "previous",
        "Smoke (one success, one expected denial)",
        "Rollback on red smoke",
    ]


#: The URL the template's stack hands the smoke: a name in the estate's private zone.
PRIVATE_URL = "https://${self:service}.${env:INTERNAL_ZONE_SUFFIX}"

#: This file's record of what the stack writes as `ApiUrl`. The template's is the private
#: name. A service that changes the output changes this too, by hand. For a public URL
#: that edit is the statement that the URL is public, which no test can read from the text.
OWN_API_URL = PRIVATE_URL

#: How a private `ApiUrl` is written: it names this variable. Searched in the JSON text
#: of the value, in these spellings, each of which Serverless v3 takes as a reference to
#: it: spaces after the colon and before the end, the address in quotes (JSON writes `"`
#: as `\"`), and the variable as a fallback source after a comma. The list is from a
#: reading of v3's parser, not from running it. A name that only starts or ends like
#: it, the name in another case and the name behind another source are other variables.
#: It also matches two texts v3 does not resolve, `${ env:...}` and an escaped `\${env:...}`;
#: both are read as private, which asks for `invoke` and cannot let `http` through.
ZONE_VARIABLE = re.compile(r"""\benv:\s*(?:\\?["'])?INTERNAL_ZONE_SUFFIX(?:\\?["'])?\s*[,}]""")

API_URL_PATH = ("resources", "Outputs", "ApiUrl")


def _api_url(serverless: object) -> tuple[object | None, str | None]:
    """(`resources.Outputs.ApiUrl.Value`, None), or (None, why this file cannot read it).

    The value is None when the stack has no such output or the output has no value.
    The output is read only where each level is a mapping written in serverless.yml
    itself. Serverless also takes `resources` as a list of fragments and as a
    `${file(...)}` reference; this file follows neither, and says so by name."""
    node = serverless
    for depth in range(len(API_URL_PATH) + 1):
        if not isinstance(node, dict):
            where = ".".join(API_URL_PATH[:depth])
            named = f"`{where}`" if where else "the top level"
            return None, (
                f"deploy/iac/serverless.yml: {named} is a {type(node).__name__}, and this "
                "file reads the ApiUrl output only where resources, Outputs and ApiUrl are "
                "each a mapping written in serverless.yml itself. Serverless also takes a "
                "list of fragments and a ${file(...)} reference there; neither is followed. "
                "Write the output inline."
            )
        if depth == len(API_URL_PATH):
            break
        node = node.get(API_URL_PATH[depth])
        if node is None:
            return None, None
    return node.get("Value"), None


def _required_transport(serverless: object) -> str | None:
    """The transport the declaration is held to, read from how the stack's `ApiUrl`
    output is WRITTEN. It is a naming convention and not a check of reachability:

    - a `Value` that names `${env:INTERNAL_ZONE_SUFFIX}`, in one of the spellings
      `ZONE_VARIABLE` lists, is a name in the estate's private zone, which a
      GitHub-hosted runner cannot resolve, so the function is asked through `invoke`;
    - any other `Value` is read as public and asked over `http`, the path a caller takes.

    A private name written another way (through `custom.*`, from SSM, as a literal, from
    another variable or a stack parameter) is therefore read as public. Nothing here can
    tell. `test_the_api_url_is_the_one_this_file_writes_down` is what stops that.

    `custom.exposure` does not decide this. `deploy/iac/app` has one ingress, the
    shared internal listener, for every value of it, and `ApiUrl` is the private
    name until a service re-points it. None when the stack has no `ApiUrl` output:
    the smoke is then handed nothing, and no transport reaches nothing."""
    value, _ = _api_url(serverless)
    if value is None:
        return None
    return "invoke" if ZONE_VARIABLE.search(json.dumps(value, default=str)) else "http"


def _transport_finding(declared: str, serverless: object) -> str | None:
    """None when the declared transport is the one the URL is written for."""
    value, unreadable = _api_url(serverless)
    if unreadable is not None:
        return unreadable
    required = _required_transport(serverless)
    if required is None:
        return "the stack has no ApiUrl output for the smoke to be handed"
    if declared == required:
        return None
    return (
        f"deploy/smoke.json declares transport {declared!r}, and the stack's ApiUrl "
        f"({value!r}) is read as needing {required!r}. This test reads "
        "how the URL is written, not where it resolves: a Value that names "
        "${env:INTERNAL_ZONE_SUFFIX} is a private name and needs 'invoke'; any other "
        "spelling is read as public and needs 'http'. If this URL is a private name written "
        "another way, write it with ${env:INTERNAL_ZONE_SUFFIX} in the output's own Value "
        "and keep 'invoke': 'http' cannot reach a private name from a GitHub-hosted runner."
    )


def _own_url_finding(serverless: object, record: object = OWN_API_URL) -> str | None:
    """None when the stack writes the `ApiUrl` this file has written down. `record` is
    `OWN_API_URL`; it is a parameter so that a test can hold the rule under another record."""
    value, unreadable = _api_url(serverless)
    if unreadable is not None:
        return unreadable
    if value == record:
        return None
    return (
        f"deploy/iac/serverless.yml writes ApiUrl as {value!r}, and OWN_API_URL in this file "
        f"is {record!r}. OWN_API_URL is this file's record of what the stack writes, so "
        "a change to the output is made in both places, on purpose. Before changing it here: "
        "the transport test reads a name as private only when its Value names "
        "${env:INTERNAL_ZONE_SUFFIX}. If the new URL is a name in the private zone and does "
        f"not name that variable, write it so that it does (the template's is {PRIVATE_URL!r}); "
        "otherwise it is read as public, and 'http' would be accepted over a name the runner "
        "cannot resolve. If it is a public URL, set OWN_API_URL to it and declare "
        '"transport": "http" in deploy/smoke.json.'
    )


def test_the_declared_smoke_transport_is_the_one_the_api_url_is_written_for() -> None:
    serverless = yaml.safe_load((ROOT / "deploy" / "iac" / "serverless.yml").read_text())
    declared = json.loads((ROOT / "deploy" / "smoke.json").read_text()).get("transport", "http")
    finding = _transport_finding(declared, serverless)
    assert finding is None, finding


def _stack(value: object) -> dict:
    return {"resources": {"Outputs": {"ApiUrl": {"Value": value}}}}


@pytest.mark.parametrize(
    "value",
    [
        PRIVATE_URL,
        PRIVATE_URL + "/v1",
        "https://${self:service}.${env:INTERNAL_ZONE_SUFFIX, 'internal.example'}",
        {"Fn::Join": ["", ["https://${self:service}.", "${env:INTERNAL_ZONE_SUFFIX}"]]},
        {
            "Fn::Sub": [
                "https://${Name}.${Zone}",
                {"Name": "${self:service}", "Zone": "${env:INTERNAL_ZONE_SUFFIX}"},
            ]
        },
        "https://${self:service}.${env: INTERNAL_ZONE_SUFFIX}",
        "https://${self:service}.${env:  INTERNAL_ZONE_SUFFIX, 'internal.example'}",
        "https://${self:service}.${env:INTERNAL_ZONE_SUFFIX }",
        "https://${self:service}.${env:INTERNAL_ZONE_SUFFIX , 'internal.example'}",
        "https://${self:service}.${env:'INTERNAL_ZONE_SUFFIX'}",
        'https://${self:service}.${env:"INTERNAL_ZONE_SUFFIX"}',
        "https://${self:service}.${self:custom.zone, env:INTERNAL_ZONE_SUFFIX}",
        "https://${self:service}.${env:ZONE_OVERRIDE, env:INTERNAL_ZONE_SUFFIX}",
    ],
    ids=[
        "template",
        "path-after-the-zone",
        "with-a-default",
        "join",
        "sub-map",
        "a-space-after-the-colon",
        "spaces-after-the-colon-and-a-default",
        "a-space-before-the-end",
        "a-space-before-the-comma",
        "the-address-in-single-quotes",
        "the-address-in-double-quotes",
        "a-fallback-after-another-source",
        "a-fallback-after-another-env",
    ],
)
@pytest.mark.parametrize("exposure", ["private", "edge_iam", "public"])
def test_a_url_that_names_the_zone_variable_needs_invoke_whatever_the_exposure_says(
    exposure: str, value: object
) -> None:
    config = {"custom": {"exposure": exposure}, **_stack(value)}
    assert _required_transport(config) == "invoke"


@pytest.mark.parametrize(
    "value",
    [
        "https://api.example.com",
        {"Fn::GetAtt": ["HttpApi", "ApiEndpoint"]},
        {"Fn::Sub": "https://${HttpApi}.execute-api.${AWS::Region}.amazonaws.com"},
        "https://${self:service}.${env:NOT_INTERNAL_ZONE_SUFFIX}",
        "https://${self:service}.${env:INTERNAL_ZONE_SUFFIX_EU}",
        "https://${self:service}.${env:INTERNAL_ZONE_SUFFIX.eu}",
        "https://${self:service}.${env:internal_zone_suffix}",
        "https://${self:service}.${awsenv:INTERNAL_ZONE_SUFFIX}",
        {"Fn::Join": ["", ["https://svc.${env:", "INTERNAL_ZONE_SUFFIX", "}"]]},
        "https://${self:custom.internalHost}",
        "https://${self:service}.${ssm:/example/zone-suffix}",
        "https://thing.svc.example.internal",
    ],
    ids=[
        "literal",
        "get-att",
        "sub",
        "a-variable-that-ends-like-it",
        "a-variable-that-starts-like-it",
        "a-variable-with-more-after-a-dot",
        "the-name-in-lower-case",
        "a-source-that-ends-like-env",
        "the-reference-split-across-join-items",
        "through-custom",
        "from-ssm",
        "a-private-literal",
    ],
)
@pytest.mark.parametrize("exposure", ["private", "edge_iam", "public"])
def test_a_url_written_any_other_way_is_read_as_public_and_asked_over_http(
    exposure: str, value: object
) -> None:
    """Rows four to nine are other variables, or no variable, for Serverless v3. The
    last three rows are the convention's limit, kept as rows so it stays in view: each
    may be a private name in truth, and each is read as public.
    `test_the_api_url_is_the_one_this_file_writes_down` is what stops a service landing
    on one of them unawares."""
    config = {"custom": {"exposure": exposure}, **_stack(value)}
    assert _required_transport(config) == "http"


@pytest.mark.parametrize(
    "value",
    [datetime.date(2026, 10, 5), {1, 2}, b"https://api.example.com"],
    ids=["a-date", "a-set", "bytes"],
)
def test_a_value_json_cannot_write_is_read_as_public_and_is_not_raised_on(value: object) -> None:
    """YAML makes each of these from an unquoted date, `!!set` and `!!binary`. None is a
    URL, and none names the variable."""
    assert _required_transport(_stack(value)) == "http"


def test_a_stack_with_no_api_url_output_has_no_transport_to_require() -> None:
    assert _required_transport({"resources": {"Outputs": {}}}) is None
    assert _required_transport({}) is None
    assert _required_transport({"resources": None}) is None
    # Another output is not the one the workflow reads.
    assert (
        _required_transport({"resources": {"Outputs": {"Other": {"Value": PRIVATE_URL}}}}) is None
    )
    # An output with no value hands the smoke nothing.
    described = {"resources": {"Outputs": {"ApiUrl": {"Description": "the URL"}}}}
    assert _required_transport(described) is None


NO_OUTPUT = "the stack has no ApiUrl output for the smoke to be handed"
HOW_THE_URL_IS_READ = (
    "This test reads how the URL is written, not where it resolves: a Value that names "
    "${env:INTERNAL_ZONE_SUFFIX} is a private name and needs 'invoke'; any other spelling "
    "is read as public and needs 'http'. If this URL is a private name written another "
    "way, write it with ${env:INTERNAL_ZONE_SUFFIX} in the output's own Value and keep "
    "'invoke': 'http' cannot reach a private name from a GitHub-hosted runner."
)


def test_a_declaration_the_url_is_written_for_is_no_finding() -> None:
    assert _transport_finding("invoke", _stack(PRIVATE_URL)) is None
    assert _transport_finding("http", _stack("https://api.example.com")) is None


def test_a_transport_named_in_another_case_is_not_the_one_the_url_is_written_for() -> None:
    """`smoke.py` reads the name exactly and refuses any other."""
    assert _transport_finding("INVOKE", _stack(PRIVATE_URL)) is not None
    assert _transport_finding("Http", _stack("https://api.example.com")) is not None
    assert _transport_finding(" invoke", _stack(PRIVATE_URL)) is not None
    assert _transport_finding("http ", _stack("https://api.example.com")) is not None


def test_invoke_over_a_private_name_written_another_way_is_told_to_keep_invoke() -> None:
    """The state a service reaches by writing its host once under `custom`. `invoke`
    works there. The message must not send it to `http`, which does not."""
    finding = _transport_finding("invoke", _stack("https://${self:custom.internalHost}"))
    assert finding == (
        "deploy/smoke.json declares transport 'invoke', and the stack's ApiUrl "
        "('https://${self:custom.internalHost}') is read as needing 'http'. " + HOW_THE_URL_IS_READ
    )


def test_http_over_the_private_name_is_told_what_it_needs() -> None:
    finding = _transport_finding("http", _stack(PRIVATE_URL))
    assert finding == (
        "deploy/smoke.json declares transport 'http', and the stack's ApiUrl "
        "('https://${self:service}.${env:INTERNAL_ZONE_SUFFIX}') is read as needing 'invoke'. "
        + HOW_THE_URL_IS_READ
    )


@pytest.mark.parametrize("declared", ["http", "invoke"])
def test_no_api_url_output_is_a_finding_whatever_is_declared(declared: str) -> None:
    assert _transport_finding(declared, {"resources": {"Outputs": {}}}) == NO_OUTPUT


@pytest.mark.parametrize(
    ("config", "where", "kind"),
    [
        ({"resources": [_stack(PRIVATE_URL)["resources"]]}, "resources", "list"),
        ({"resources": "${file(./outputs.yml)}"}, "resources", "str"),
        ({"resources": []}, "resources", "list"),
        ({"resources": {"Outputs": ""}}, "resources.Outputs", "str"),
        ({"resources": {"Outputs": "${file(./outputs.yml)}"}}, "resources.Outputs", "str"),
        (
            {"resources": {"Outputs": [{"ApiUrl": {"Value": PRIVATE_URL}}]}},
            "resources.Outputs",
            "list",
        ),
        ({"resources": {"Outputs": {"ApiUrl": PRIVATE_URL}}}, "resources.Outputs.ApiUrl", "str"),
    ],
    ids=[
        "fragments",
        "file",
        "no-fragments",
        "outputs-an-empty-string",
        "outputs-from-a-file",
        "outputs-as-a-list",
        "a-bare-value",
    ],
)
def test_an_output_this_file_cannot_read_is_said_by_name(
    config: dict, where: str, kind: str
) -> None:
    """Serverless accepts the fragments, the two file references and the list of
    outputs. Read as mappings they raise an AttributeError or a TypeError, which is a red
    test that names no finding. An empty list or string is a shape too, not an absence."""
    value, unreadable = _api_url(config)
    assert value is None
    assert unreadable == (
        f"deploy/iac/serverless.yml: `{where}` is a {kind}, and this file reads the ApiUrl "
        "output only where resources, Outputs and ApiUrl are each a mapping written in "
        "serverless.yml itself. Serverless also takes a list of fragments and a ${file(...)} "
        "reference there; neither is followed. Write the output inline."
    )


def test_both_tests_that_read_the_real_file_say_an_unreadable_output_by_that_sentence() -> None:
    fragments = {"resources": [_stack(PRIVATE_URL)["resources"]]}
    said = _api_url(fragments)[1]
    assert said is not None
    assert said.startswith("deploy/iac/serverless.yml: `resources` is a list, and this file")
    assert _transport_finding("invoke", fragments) == said
    assert _transport_finding("http", fragments) == said
    assert _own_url_finding(fragments) == said


@pytest.mark.parametrize(
    ("config", "kind"),
    [(None, "NoneType"), ([{"resources": {}}], "list"), ("service: example", "str")],
    ids=["an-empty-file", "a-list", "a-string"],
)
def test_a_file_whose_top_level_is_not_a_mapping_is_said_by_name(config: object, kind: str) -> None:
    """No stack Serverless deploys, and still a file `yaml.safe_load` returns."""
    said = (
        f"deploy/iac/serverless.yml: the top level is a {kind}, and this file reads the ApiUrl "
        "output only where resources, Outputs and ApiUrl are each a mapping written in "
        "serverless.yml itself. Serverless also takes a list of fragments and a ${file(...)} "
        "reference there; neither is followed. Write the output inline."
    )
    assert _api_url(config) == (None, said)
    assert _required_transport(config) is None
    assert _transport_finding("invoke", config) == said
    assert _own_url_finding(config) == said


def test_an_output_this_file_can_read_is_read() -> None:
    """The control for the rows above."""
    assert _api_url(_stack(PRIVATE_URL)) == (PRIVATE_URL, None)
    assert _api_url({}) == (None, None)


def test_the_api_url_is_the_one_this_file_writes_down() -> None:
    """Two jobs. In the template it is the control for the parametrized tests above: the
    URL they call private is the one this template deploys. In a service it is what stops
    a private name written another way, which the transport test reads as public and
    would accept `http` over: the output cannot change without this file's record
    changing, and the message says what to check first."""
    serverless = yaml.safe_load((ROOT / "deploy" / "iac" / "serverless.yml").read_text())
    finding = _own_url_finding(serverless)
    assert finding is None, finding


@pytest.mark.parametrize(
    "value",
    ["https://not-the-one-written-down.example", "https://${self:custom.notWrittenDown}"],
)
def test_an_api_url_that_is_not_the_one_written_down_is_told_what_to_check(value: str) -> None:
    """Written so that it holds in a service that has set OWN_API_URL to its own URL:
    the rows are values no service writes, and the record is quoted from the constant."""
    assert _own_url_finding(_stack(OWN_API_URL)) is None
    assert _own_url_finding(_stack(value)) == (
        f"deploy/iac/serverless.yml writes ApiUrl as '{value}', and OWN_API_URL in this file "
        f"is {OWN_API_URL!r}. OWN_API_URL is this file's record of what the stack writes, so "
        "a change to the output is made in both places, on purpose. Before changing it here: "
        "the transport test reads a name as private only when its Value names "
        "${env:INTERNAL_ZONE_SUFFIX}. If the new URL is a name in the private zone and does "
        "not name that variable, write it so that it does (the template's is "
        "'https://${self:service}.${env:INTERNAL_ZONE_SUFFIX}'); otherwise it is read as "
        "public, and 'http' would be accepted over a name the runner cannot resolve. If it is "
        'a public URL, set OWN_API_URL to it and declare "transport": "http" in '
        "deploy/smoke.json."
    )


def test_the_record_decides_and_not_the_templates_url() -> None:
    """A record changed with the output left as it was is a finding, like the reverse."""
    public = "https://api.example.com"
    assert _own_url_finding(_stack(public), record=public) is None
    finding = _own_url_finding(_stack(PRIVATE_URL), record=public)
    assert finding is not None
    assert "OWN_API_URL in this file is 'https://api.example.com'" in finding


def test_a_stack_with_no_api_url_output_is_not_the_one_written_down() -> None:
    finding = _own_url_finding({"resources": {"Outputs": {}}})
    assert finding is not None
    assert finding.startswith("deploy/iac/serverless.yml writes ApiUrl as None, and OWN_API_URL")


def test_the_rollback_runs_after_a_landed_deploy_with_the_recorded_target() -> None:
    steps = _deploy_steps()
    smoke = _index(steps, "service-kit/ci/smoke.py")
    rollback = _index(steps, "lambda_release.sh rollback")
    assert rollback > smoke
    condition = steps[rollback].get("if", "")
    assert "failure()" in condition, "a rollback that runs on success is a redeploy of the past"
    assert "steps.deploy.outcome == 'success'" in condition
    env = steps[rollback]["env"]
    assert env["PREVIOUS_IMAGE"] == "${{ steps.previous.outputs.image_uri }}"
    assert '"$PREVIOUS_IMAGE"' in steps[rollback]["run"]

    deploy_env = next(s for s in steps if s.get("id") == "deploy")["env"]
    missing = sorted(k for k in deploy_env if k != "IMAGE_URI" and env.get(k) != deploy_env[k])
    assert missing == [], f"the rollback redeploy would resolve these as empty: {missing}"


def _deploy_env_module():
    spec = importlib.util.spec_from_file_location(
        "deploy_env", ROOT / "service-kit" / "ci" / "deploy_env.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_declared_configuration_is_read_before_the_push_and_wraps_both_deploys() -> None:
    """Regression: serverless.yml variables with no default reach the deploy AND
    the rollback from SSM / Secrets Manager, and an absent one stops the job before
    an image is pushed."""
    steps = _deploy_steps()
    check = _index(steps, "deploy_env.py check")
    assert check < _index(steps, "docker push"), "an absent parameter must stop the job first"
    deploy = next(s for s in steps if s.get("id") == "deploy")
    assert steps[check]["env"]["STAGE"] == deploy["env"]["STAGE"]
    for run in (deploy["run"], steps[_index(steps, "lambda_release.sh rollback")]["run"]):
        wrapper = run.find('deploy_env.py exec "$STAGE" --')
        target = max(run.find("serverless@3 deploy"), run.find("lambda_release.sh rollback"))
        assert 0 <= wrapper < target, f"not run through deploy_env.py exec: {run!r}"


def test_every_no_default_serverless_variable_has_exactly_one_source() -> None:
    """The authoring side of an earlier change, measured on THIS repository's own files:
    each `${env:NAME}` serverless.yml resolves with no default is set by the deploy
    step or declared in deploy/deploy_env.json -- never both, since a step value for a
    declared variable is that variable in plain text."""
    deploy_env = _deploy_env_module()
    declared = deploy_env.load_spec(deploy_env.DEFAULT_SPEC, "dev")
    required = deploy_env.required_by(deploy_env.DEFAULT_SERVERLESS)
    assert "IMAGE_URI" in required, "the parser read nothing from serverless.yml"
    steps = _deploy_steps()
    # Both clauses hold for every step that runs its command through `deploy_env.py
    # exec`, which refuses a declared variable that is also a step value and a needed
    # variable that is neither: the deploy, and the rollback, where a refusal means no
    # rollback.
    wrapped = [s for s in steps if "deploy_env.py exec" in str(s.get("run", ""))]
    assert [s.get("name") for s in wrapped] == [
        "Deploy Lambda (serverless)",
        "Rollback on red smoke",
    ]
    for step in wrapped:
        assert sorted(set(required) - set(step["env"]) - set(declared)) == [], step["name"]
        assert sorted(set(declared) & set(step["env"])) == [], step["name"]
