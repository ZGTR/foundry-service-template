"""The data-tool policy and use case, against in-memory ports (the SQL path is in poc/)."""

from __future__ import annotations

import pytest

from foundry_data_gateway.adapters.fake.data_access import FixedRowsDataSource, InMemoryReceiptStore
from foundry_data_gateway.application.run_data_tool import RunDataTool
from foundry_data_gateway.domain.data_tools import (
    CATALOGUE,
    DataPolicy,
    ToolRefused,
    check_arguments,
)

POLICY = DataPolicy.from_json(
    '{"org-a": {"tools": ["list_subjects", "struggling_learners"], "max_rows": 2}}'
)


def run(rows, tool="struggling_learners", args=None, org="org-a"):
    source, receipts = FixedRowsDataSource(rows), InMemoryReceiptStore()
    use_case = RunDataTool(POLICY, source, receipts)
    try:
        result = use_case.execute(
            org_id=org,
            subject="u",
            tool_name=tool,
            args={"below_score": 60} if args is None else args,
            call_id="c1",
            channel="mcp",
        )
    except ToolRefused as refused:
        return refused, source, receipts
    return result, source, receipts


def test_default_deny_for_an_unknown_org_and_an_ungranted_tool():
    assert POLICY.allowed("org-z") == []
    with pytest.raises(ToolRefused) as unknown:
        POLICY.decide("org-z", "list_subjects")
    assert unknown.value.rule == "unknown_org"
    with pytest.raises(ToolRefused) as ungranted:
        POLICY.decide("org-a", "subject_progress")
    assert ungranted.value.rule == "tool_not_granted"


@pytest.mark.parametrize(
    "args, rule_text",
    [
        ([], "one JSON object"),
        ({}, "missing"),
        ({"below_score": "x"}, "integer"),
        ({"below_score": 101}, "above"),
        ({"below_score": 5, "sql": "DROP"}, "unexpected"),
    ],
)
def test_arguments_outside_the_schema_are_refused(args, rule_text):
    with pytest.raises(ToolRefused, match=rule_text):
        check_arguments(CATALOGUE["struggling_learners"], args)


def test_a_refusal_reads_nothing_and_still_writes_a_receipt():
    refused, source, receipts = run([], tool="subject_progress", args={"subject_slug": "x"})
    assert isinstance(refused, ToolRefused) and source.reads == 0
    assert [(r.decision, r.rule) for r in receipts.receipts] == [("refuse", "tool_not_granted")]


def test_more_rows_than_the_cap_is_refused_not_truncated():
    row = {"learner": "a", "subject": "s", "avg_score": 1.0, "attempts": 1}
    refused, _, receipts = run([row, row, row])
    assert refused.rule == "row_cap" and receipts.receipts[0].decision == "refuse"


def test_an_undeclared_column_never_leaves():
    refused, _, _ = run([{"learner": "a", "parent_email": "p@x"}])
    assert refused.rule == "undeclared_column"


def test_an_allowed_read_returns_rows_and_an_allow_receipt():
    row = {"learner": "a", "subject": "s", "avg_score": 1.0, "attempts": 1}
    result, _, receipts = run([row])
    assert result.as_json()["row_count"] == 1
    assert (receipts.receipts[0].decision, receipts.receipts[0].rows) == ("allow", 1)
