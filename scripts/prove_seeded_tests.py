"""Prove each seeded suite goes RED when the behaviour it tests is neutralised.

Prints the BASELINE and RESTORE arms as well as the mutated one: an all-red
table is indistinguishable from a broken selector, and the mutated arms alone
are exactly the half that agrees with the hoped-for conclusion. Also prints the
COLLECTED count per arm — "0 collected" is the tell that the selector matched
nothing and the run measured neither red nor green.

EVERY MUTATION DECLARES THE PROFILES IT APPLIES TO, and a run under a profile a
mutation does not apply to is a COUNTED SKIP carrying its reason — never a
silent omission and never a pass. That is the half of `--profile` that costs
something to build, and it is the half that makes the flag mean anything. The
flag alone selects which arm runs; without the declaration, a one-profile table
reports a total that a reader takes for the whole population, so an arm that
never ran is indistinguishable from an arm that ran and passed. Both runs print
the union explicitly — how many arms this profile proved, how many it declined
and why, and which profile does prove those — because the claim "the seeded tier
is mutation-proved" is a claim about the union and NEITHER RUN ALONE ESTABLISHES
IT.
"""
import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PY = sys.executable

_parser = argparse.ArgumentParser(description=__doc__)
_parser.add_argument(
    "--profile",
    default=os.environ.get("SEED_PROFILE", "fake"),
    choices=["fake", "real"],
    help=(
        "which seed profile every arm runs under. An APPLICATION-layer mutation is "
        "behavioural and must redden under both arms; one that reddens only on fakes "
        "means the real arm is not exercising the code path, which is the failure "
        "this flag exists to find. An ADAPTER-layer mutation is a different case and "
        "not that failure: `SqlWidgetRepository` and `InMemoryWidgetRepository` are "
        "two implementations, so a mutation of one is INAPPLICABLE under the other "
        "profile rather than a gap -- its anchor is in code the other arm never "
        "loads. Those arms declare their profile and are reported as counted skips."
    ),
)
_args = _parser.parse_args()
OTHER_PROFILE = "real" if _args.profile == "fake" else "fake"

#: Passed to every arm, so the baseline, the mutant and the restore all run under
#: the SAME profile. Setting it once in the ambient environment would work here
#: and would break the moment somebody ran two profiles in one process.
ENV = {**os.environ, "SEED_PROFILE": _args.profile}
if _args.profile == "real":
    # AN UNAVAILABLE REAL RESOURCE MUST NOT LOOK LIKE A PROVEN ARM. Without
    # this the kit skips the real fixtures, pytest exits 0 for baseline AND
    # mutant, and the arm reports NOT PROVEN -- correct, but with a diagnosis
    # pointing at the mutation rather than at the missing database. `=1` turns
    # the skip into a `pytest.UsageError` at configure time, which names the
    # resource.
    ENV["REQUIRE_REAL_RESOURCES"] = "1"

#: Which profiles an arm's anchor is reachable under. Spelled out per mutation
#: rather than inferred from the file's path: `adapters/real/` and
#: `adapters/fake/` happen to say it in this template, and a service whose two
#: implementations live anywhere else would get a silently wrong table from a
#: path rule. The declaration is the contract; the path is a coincidence.
BOTH = ("fake", "real")
FAKE_ONLY = ("fake",)
REAL_ONLY = ("real",)

#: Why an arm is inapplicable, printed with the skip. A skip with no reason is
#: the defect this table exists to prevent -- it reads as an arm someone forgot.
INAPPLICABLE = {
    FAKE_ONLY: (
        "the anchor is in `adapters/fake/`, which the real arm never loads; "
        "the equivalent real-arm behaviour has its own arms in this table"
    ),
    REAL_ONLY: (
        "the anchor is in `adapters/real/`, which the fake arm never loads; "
        "these are the SQL adapter's OWN arms and no fake-profile run can take them"
    ),
}

MUTATIONS = [
    (
        "EC6 tenancy: fetch a widget by id alone, ignoring the caller's org",
        "src/foundry_service_template/application/get_widget.py",
        "widget = self._repository.get(principal.org_id, widget_id)",
        'widget = self._repository.get("org_other", widget_id)  # MUTANT',
        ["tests/seeded", "tests/e2e"],
        BOTH,
    ),
    (
        "EC6 tenancy: list every org's widgets, not the caller's",
        "src/foundry_service_template/application/list_widgets.py",
        "return self._repository.list_for_org(principal.org_id)",
        'return self._repository.list_for_org("org_other")  # MUTANT',
        ["tests/seeded"],
        BOTH,
    ),
    (
        "EC6 provenance: take the new widget's org from somewhere other than the principal",
        "src/foundry_service_template/application/create_widget.py",
        "org_id=principal.org_id,",
        'org_id="org_other",  # MUTANT',
        ["tests/seeded", "tests/e2e"],
        BOTH,
    ),
    (
        # THE ARM THE STORE INSPECTOR EXISTS FOR. The handler still builds and
        # returns a 201 carrying a widget_id; nothing is written. A suite that
        # only reads responses back through the same service would have to find
        # this through a later read -- which is exactly the read this template's
        # own adapters make agree with the write.
        "persistence: answer 201 and never send the write to the store",
        "src/foundry_service_template/application/create_widget.py",
        "            self._repository.add(widget)",
        "            pass  # MUTANT: the reply is built, the row is not written",
        ["tests/seeded", "tests/e2e"],
        BOTH,
    ),
    (
        "SQL adapter: read a widget under the wrong half of the composite key",
        "src/foundry_service_template/adapters/real/sql_widget_repository.py",
        'record = session.get(WidgetRecord, {"org_id": org_id, "widget_id": widget_id})',
        'record = session.get(WidgetRecord, '
        '{"org_id": "org_other", "widget_id": widget_id})  # MUTANT',
        ["tests/seeded"],
        REAL_ONLY,
    ),
    (
        "SQL adapter: drop the tenancy predicate from the listing query",
        "src/foundry_service_template/adapters/real/sql_widget_repository.py",
        ".where(WidgetRecord.org_id == org_id)",
        '.where(WidgetRecord.org_id == "org_other")  # MUTANT',
        ["tests/seeded"],
        REAL_ONLY,
    ),
    (
        "SQL adapter: store the name the caller did not send",
        "src/foundry_service_template/adapters/real/sql_widget_repository.py",
        "        name=widget.name,",
        '        name="mutated",  # MUTANT',
        ["tests/seeded"],
        REAL_ONLY,
    ),
    (
        "in-memory adapter: match a widget id under any org",
        "src/foundry_service_template/adapters/fake/widget_repository.py",
        "        return self._by_key.get((org_id, widget_id))",
        "        return next((w for (_, k), w in self._by_key.items() "
        "if k == widget_id), None)  # MUTANT",
        ["tests/seeded"],
        FAKE_ONLY,
    ),
]

#: pytest's terminal summary line, e.g. "4 failed, 19 passed in 1.20s". Requiring
#: a COUNT in front of the word is what separates the real summary from prose that
#: merely contains it.
SUMMARY = re.compile(r"\b\d+ (?:passed|failed|error|errors|skipped)\b")


def collected_count(paths):
    """How many tests the selector matches, asked DIRECTLY rather than parsed.

    The summary line is not reliable: measured on the service template, a `-q`
    run of 23 passing tests printed the dots, a warnings block and no "N passed"
    line at all, so a parser keyed on it read 0 collected and the prover
    (correctly) refused to claim proof of anything. `--collect-only -q` prints
    one line per test and its exit code is authoritative, so this asks the
    question it actually wants answered.
    """
    result = subprocess.run(
        [PY, "-m", "pytest", *paths, "--collect-only", "-q", "-p", "no:cacheprovider"],
        cwd=REPO, capture_output=True, text=True, env=ENV,
    )
    if result.returncode not in (0, 1):
        return 0
    # Two shapes in the wild, and this has to read both: older pytest prints one
    # `path::test` line per test, pytest 9 prints `path: N` per file. Counting
    # only the first shape reports 0 on the second, which this script then
    # reports as an unmeasured arm -- a false alarm that is just as useless as
    # the false proof it exists to prevent.
    ids = 0
    totals = 0
    for line in result.stdout.splitlines():
        if line.startswith(("=", "-", " ")) or not line.strip():
            continue
        if "::" in line:
            ids += 1
            continue
        match = re.fullmatch(r"\S+: (\d+)", line.strip())
        if match:
            totals += int(match.group(1))
    return ids or totals


def run(paths):
    """Pass/fail from the EXIT CODE, which is pytest's own verdict.

    Never from the summary text. An instrument that reports on a run by parsing
    the run's own prose is one formatting change away from lying, and this
    script exists precisely to catch instruments that report the wrong thing.
    """
    # NO `-q` HERE, deliberately, and this is not a style choice.
    #
    # This template's own pyproject.toml carries no `[tool.pytest.ini_options]`, so
    # it is not an inifile candidate and pytest CLIMBS for one. Run from the
    # Foundry monorepo, where the template is pinned as a submodule, it lands on
    # `platform/pyproject.toml`, whose `addopts` begins `-q`. Verbosity
    # flags ACCUMULATE, so a `-q` added here made an effective `-qq` and pytest
    # printed no summary line at all -- measured in this checkout: 23 passing
    # tests, dots, a warnings block, and nothing else. The same command in an
    # emitted standalone service printed "23 passed", because there the rootdir is
    # the service itself and no addopts exists. One script, two answers, decided by
    # a config file two directories above the one being tested.
    #
    # The arms stay readable without it, and `collected_count` below is what the
    # verdict actually rests on, so this only costs stdout we already capture.
    result = subprocess.run(
        [PY, "-m", "pytest", *paths, "--no-header", "-p", "no:cacheprovider"],
        cwd=REPO, capture_output=True, text=True, env=ENV,
    )
    # ANCHORED to pytest's own summary shape, not to the words "passed"/"failed"
    # anywhere in the output. Measured on this template: under a red arm pytest
    # prints the failing test's source, and a docstring in `tests/e2e` contains
    # the phrase "seeded nothing and added one", which is what an unanchored
    # filter reported as the run's summary. A garbage string in the summary
    # column is worse than an empty one: it reads like a measurement.
    tail = [line for line in result.stdout.splitlines() if SUMMARY.search(line)]
    summary = tail[-1].strip() if tail else "(no summary line; exit code is the verdict)"
    return result.returncode, summary, collected_count(paths)


def arm(label, paths):
    code, summary, collected = run(paths)
    verdict = "GREEN" if code == 0 else "RED"
    # The COLLECTED count is printed, never merely checked. An arm that matched
    # nothing exits 1 and reads exactly like an arm that matched and failed, so
    # the number a reader needs to tell those apart has to be on the line.
    flag = "  <-- SELECTOR MATCHED NOTHING" if collected == 0 else ""
    print(
        f"    {label:<10} exit={code} {verdict:<5} collected={collected:<3} [{summary}]{flag}"
    )
    return code, collected


failures = []
proven = []
skipped = []
print(f"seed profile: {_args.profile}")
for title, relative, old, new, paths, profiles in MUTATIONS:
    path = REPO / relative
    print(f"\n=== {title}")
    print(f"    file: {relative}")
    if _args.profile not in profiles:
        # A COUNTED SKIP, with the reason and the profile that does take it. An
        # arm silently left out of a one-profile table is indistinguishable from
        # an arm that ran and passed, and the total at the bottom is what a
        # reader quotes.
        print(
            f"    SKIP  applies to {'/'.join(profiles)} only — {INAPPLICABLE[profiles]}"
        )
        print(f"    => DECLARED SKIP (proved by `--profile {profiles[0]}`)")
        skipped.append((title, profiles[0]))
        continue
    original = path.read_text()
    occurrences = original.count(old)
    if occurrences == 0:
        print("    !! ANCHOR NOT FOUND — mutation not applied, arm is UNMEASURED")
        failures.append(title)
        continue
    if occurrences > 1:
        # `str.replace(old, new, 1)` silently takes the FIRST occurrence, which
        # may not be the site the arm is about. Measured on governance-service:
        # an arm anchored on `raise ConfigurationError(` -- six of them in that
        # file -- mutated an unrelated one and read GREEN, which is
        # indistinguishable from "the behaviour is not covered". A non-unique
        # anchor is therefore an UNMEASURED arm, not a working one.
        print(
            f"    !! ANCHOR NOT UNIQUE ({occurrences} occurrences) — the first one "
            f"would be mutated, which may not be the site under test. UNMEASURED."
        )
        failures.append(title)
        continue

    base_code, base_n = arm("baseline", paths)
    mutated = original.replace(old, new, 1)
    # ASSERT THE MUTATION LANDED before reading any exit code from it. A
    # `str.replace` that matched nothing rewrites nothing, and an arm that never
    # mutated reads exactly like a clause that survived. The uniqueness check
    # above makes this redundant today and free tomorrow.
    if mutated == original:
        print("    !! MUTATION DID NOT CHANGE THE FILE — arm is UNMEASURED")
        failures.append(title)
        continue
    path.write_text(mutated)
    try:
        mutant_code, mutant_n = arm("mutant", paths)
    finally:
        path.write_text(original)
    restore_code, restore_n = arm("restore", paths)

    ok = (
        base_code == 0 and restore_code == 0 and mutant_code != 0
        and base_n > 0 and mutant_n > 0 and restore_n > 0
    )
    print(f"    => {'PROVEN' if ok else 'NOT PROVEN'}")
    if ok:
        proven.append(title)
    else:
        failures.append(title)

print("\n" + "=" * 70)

# EVERY ARM IS IN EXACTLY ONE STATE, and the arithmetic is checked rather than
# printed. A total that does not equal its own parts is the shape a per-arm log
# exists to prevent, and a coverage line is the last place it should appear.
accounted = len(proven) + len(skipped) + len(failures)
if accounted != len(MUTATIONS):
    print(
        f"REFUSE: {accounted} arm(s) accounted for against {len(MUTATIONS)} declared. "
        f"An arm in no state is an instrument fault, not a finding."
    )
    sys.exit(2)

if failures:
    print(f"NOT PROVEN ({len(failures)}):")
    for item in failures:
        print("  -", item)
    sys.exit(1)

# THE UNION, STATED. This run proved `len(proven)` arms and DECLINED
# `len(skipped)`, and the declined ones are named with the profile that takes
# them. The sentence a reader wants -- "the seeded tier is mutation-proved" --
# is true of the union and of neither run alone, so the union is printed here
# rather than left to be assembled from two logs by whoever reads them.
print(
    f"{len(proven)} proven + {len(skipped)} declared skip(s) under "
    f"SEED_PROFILE={_args.profile}, union {len(MUTATIONS)}/{len(MUTATIONS)} "
    f"across both profiles — neither run alone is."
)
if skipped:
    print(f"\nNOT RUN HERE ({len(skipped)}); `--profile {OTHER_PROFILE}` takes them:")
    for title, owner in skipped:
        print(f"  - [{owner}] {title}")
else:
    print(
        "\nNOT RUN HERE (0): every declared arm applies under this profile, so the "
        f"union above is this run. `--profile {OTHER_PROFILE}` would prove the same "
        "arms against the other store and is still worth running."
    )
