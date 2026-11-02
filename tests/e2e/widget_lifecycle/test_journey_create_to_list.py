"""The widget journey, driven by its own responses. The template to copy.

The rule that makes this an e2e test and not three seeded tests in a row: every
step after the first takes its inputs from the PREVIOUS step's RESPONSE, never
from the fixture. Nothing here reads an id out of `tests/fixtures/` — the ids
come from what the service actually returned.

That is what covers the SEAMS. A field renamed on one side of the
create/fetch boundary leaves both endpoints' own tests green and breaks this
one, which is the only reason to pay for it.
"""

from __future__ import annotations

from foundry_testkit.seeded_case import SeededAPICase

CORPUS = ["widgets"]

ORG = "org_seed"
OTHER_ORG = "org_other"


def headers(org: str = ORG) -> dict[str, str]:
    return {"x-org-id": org, "x-subject-id": "user_1", "x-scopes": "widgets:read,widgets:write"}


class TestWidgetLifecycleJourney(SeededAPICase):
    seed_sources = CORPUS

    def test_create_then_fetch_then_list_carries_the_same_identity(self):
        # 1. Create, and let the SERVICE choose the id.
        created = self.client.post("/widgets", json={"name": "Sprocket"}, headers=headers())
        assert created.status_code == 201
        body = created.json()

        # 2. Fetch the id the service returned -- not one the fixture knows.
        fetched = self.client.get(f"/widgets/{body['widget_id']}", headers=headers())
        assert fetched.status_code == 200
        # The seam: the two representations must agree field for field, or a
        # caller that creates and then re-reads sees two different widgets.
        assert fetched.json() == body

        # 3. The listing must contain exactly that id, alongside the corpus.
        listed = self.client.get("/widgets", headers=headers()).json()["widgets"]
        by_id = {item["widget_id"]: item for item in listed}
        assert body["widget_id"] in by_id
        assert by_id[body["widget_id"]]["name"] == body["name"]

    def test_the_journey_adds_exactly_one_widget_to_the_seeded_corpus(self):
        """Counted against what the CORPUS declared, not a literal.

        A hardcoded count breaks whenever the fixture grows for an unrelated
        reason, and — worse — cannot tell "seeded three and added one" from
        "seeded nothing and added one", which is what a silently failed seed
        looks like.
        """
        declared = len([
            row for row in self.seeded.rows("foundry_service_template.domain:Widget")
            if row["org_id"] == ORG
        ])
        before = self.client.get("/widgets", headers=headers()).json()["widgets"]
        assert len(before) == declared, "the corpus did not reach the repository"

        self.client.post("/widgets", json={"name": "One more"}, headers=headers())
        after = self.client.get("/widgets", headers=headers()).json()["widgets"]
        assert len(after) == declared + 1

    def test_a_second_org_cannot_continue_the_first_orgs_journey(self):
        created = self.client.post("/widgets", json={"name": "Private"}, headers=headers())
        widget_id = created.json()["widget_id"]

        # A real id, handed out by the service, and still unreachable from the
        # other tenant. This is the leak that a 403 would also disclose, so it
        # must be a 404.
        assert self.client.get(
            f"/widgets/{widget_id}", headers=headers(OTHER_ORG)
        ).status_code == 404

        # The control: without it, a route broken for EVERYONE would pass the
        # assertion above and read as isolation working.
        assert self.client.get(
            f"/widgets/{widget_id}", headers=headers(ORG)
        ).status_code == 200

    def test_a_refused_create_leaves_the_corpus_untouched(self):
        before = self.client.get("/widgets", headers=headers()).json()["widgets"]
        refused = self.client.post("/widgets", json={"name": ""}, headers=headers())
        assert refused.status_code == 422
        after = self.client.get("/widgets", headers=headers()).json()["widgets"]
        assert len(after) == len(before)
