"""`/widgets`, over a declared corpus. The template every new service copies.

This class is what `scripts/new_service.sh` emits as the shape to follow, so it
is written to be read as much as to pass. Two things make it a SEEDED test
rather than a unit test:

* it names a corpus it did not build (`seed_sources`), and
* every assertion is about API behaviour over that corpus.

If an assertion would still hold with the fixture deleted, the test belongs in
`tests/unit/`.

The `EC` codes are the universal edge taxonomy. Naming the code in the test id
is what makes a missing case findable by grep instead of by reading every test,
and `scripts/check_seeded_tests.py` fails when one is neither covered nor
waived WITH A REASON. Replace `Widget` with your resource and keep the shape.
"""

from __future__ import annotations

import pytest
from foundry_testkit.seeded_case import SeededAPICase

CORPUS = ["widgets"]

WIDGET = "foundry_service_template.domain:Widget"

#: The corpus declares two orgs. `org_other` exists so EC6 is answerable by
#: default rather than by somebody remembering to add a second tenant later.
ORG = "org_seed"
OTHER_ORG = "org_other"


def headers(org: str = ORG, *, scopes: str = "widgets:read,widgets:write") -> dict[str, str]:
    return {"x-org-id": org, "x-subject-id": "user_1", "x-scopes": scopes}


class TestWidgetsAPI(SeededAPICase):
    seed_sources = CORPUS

    # -- corpus access ------------------------------------------------------

    def seeded_rows(self, org: str = ORG) -> list[dict]:
        return [row for row in self.seeded.rows(WIDGET) if row["org_id"] == org]

    def list_widgets(self, org: str = ORG, **kwargs):
        return self.client.get("/widgets", headers=headers(org, **kwargs))

    def get_widget(self, widget_id: str, org: str = ORG):
        return self.client.get(f"/widgets/{widget_id}", headers=headers(org))

    # -- EC1 empty result set ----------------------------------------------

    def test_ec1_an_org_with_no_widgets_lists_empty_rather_than_erroring(self):
        response = self.list_widgets("org_with_nothing_seeded")
        assert response.status_code == 200
        assert response.json()["widgets"] == []

    # -- EC2 exactly one element -------------------------------------------

    def test_ec2_the_other_org_sees_exactly_its_own_single_widget(self):
        rows = self.seeded_rows(OTHER_ORG)
        assert len(rows) == 1, "the corpus no longer has a single-widget org"
        listed = self.list_widgets(OTHER_ORG).json()["widgets"]
        assert [item["widget_id"] for item in listed] == [rows[0]["widget_id"]]

    # -- EC3 limit boundary -------------------------------------------------

    def test_ec3_every_seeded_widget_for_the_org_is_listed(self):
        expected = {row["widget_id"] for row in self.seeded_rows()}
        listed = {item["widget_id"] for item in self.list_widgets().json()["widgets"]}
        assert listed == expected

    # -- EC4 malformed input ------------------------------------------------

    def test_ec4_a_body_missing_the_name_is_422_not_500(self):
        response = self.client.post("/widgets", json={}, headers=headers())
        assert response.status_code == 422

    def test_ec4_a_wrongly_typed_name_is_422_not_500(self):
        response = self.client.post("/widgets", json={"name": 7}, headers=headers())
        assert response.status_code == 422

    # -- EC5 referenced entity absent ---------------------------------------

    def test_ec5_an_unknown_widget_is_404(self):
        assert self.get_widget("widget_does_not_exist").status_code == 404

    # -- EC6 cross-tenant ---------------------------------------------------

    def test_ec6_one_org_cannot_read_another_orgs_widget(self):
        theirs = self.seeded_rows(OTHER_ORG)[0]["widget_id"]
        # 404, not 403: answering 403 would confirm the widget exists, which is
        # itself the cross-tenant disclosure.
        assert self.get_widget(theirs, org=ORG).status_code == 404

    def test_ec6_a_listing_never_includes_another_orgs_widget(self):
        foreign = {row["widget_id"] for row in self.seeded_rows(OTHER_ORG)}
        listed = {item["widget_id"] for item in self.list_widgets().json()["widgets"]}
        assert listed & foreign == set()

    def test_ec6_the_org_comes_from_the_principal_and_not_from_the_body(self):
        created = self.client.post(
            "/widgets", json={"name": "Forged"}, headers=headers(OTHER_ORG)
        )
        assert created.status_code == 201
        assert created.json()["org_id"] == OTHER_ORG

    # -- EC7 duplicate submit -----------------------------------------------

    def test_ec7_creating_the_same_name_twice_yields_two_distinct_widgets(self):
        first = self.client.post("/widgets", json={"name": "Twin"}, headers=headers())
        second = self.client.post("/widgets", json={"name": "Twin"}, headers=headers())
        assert first.status_code == second.status_code == 201
        # The name is not an identity. Collapsing them would silently discard
        # the second caller's widget.
        assert first.json()["widget_id"] != second.json()["widget_id"]

    # -- the STORE, not the response ----------------------------------------

    def test_a_created_widget_is_in_the_store_and_not_only_in_the_reply(self):
        """THE ARM THAT SEPARATES "the handler answered" FROM "the write landed".

        Every other test in this file asserts on an HTTP body, and a body is
        built by the handler -- so a service that answered 201 from a cache, an
        idempotency record or an in-flight object while the row was never
        written satisfies all of them. `self.rows` asks the store instead, on a
        connection the service is not using under `real` and past the
        repository's own read methods under `fake`.

        It runs under BOTH profiles, which is the point: one implementation
        reads PostgreSQL and the other projects the dict, and they answer the
        same question in the same vocabulary. A real-arm-only version of this
        test would also break `verify.sh`, which refuses a run whose two
        profiles collect different numbers of tests.
        """
        before = self.rows.count("widgets")
        created = self.client.post("/widgets", json={"name": "Persisted"}, headers=headers())
        assert created.status_code == 201

        assert self.rows.count("widgets") == before + 1
        stored = [row for row in self.rows.widgets(ORG) if row["name"] == "Persisted"]
        assert len(stored) == 1, f"the reply said 201 and the store holds {stored}"
        # The id the CALLER was given, found in the store under that name. A
        # test that only counted rows would pass against a write that stored a
        # different widget than the one it answered with.
        assert stored[0]["widget_id"] == created.json()["widget_id"]

    def test_the_store_inspector_refuses_a_table_this_service_does_not_own(self):
        """The control that makes a zero count mean something.

        `count("widgts")` is `0` from a dict and an error from PostgreSQL, and
        `0` is a legal answer a test will happily assert -- so the kit's
        `assert_owned` makes both arms refuse. Without this arm the refusal is
        a clause nothing exercises, and a typo in a row assertion would read as
        "the table is empty" on whichever profile the author ran.
        """
        with pytest.raises(AssertionError, match="not one of this service's tables"):
            self.rows.count("widgts")

    # -- EC8 unicode --------------------------------------------------------

    def test_ec8_a_multibyte_name_round_trips_byte_identically(self):
        name = "naïve café ☕ 日本語 سياسة"
        created = self.client.post("/widgets", json={"name": name}, headers=headers())
        assert created.status_code == 201
        fetched = self.get_widget(created.json()["widget_id"])
        assert fetched.json()["name"] == name

    # -- EC9 ordering determinism -------------------------------------------

    def test_ec9_the_listing_order_is_stable_across_identical_calls(self):
        runs = [
            tuple(item["widget_id"] for item in self.list_widgets().json()["widgets"])
            for _ in range(4)
        ]
        assert len(set(runs)) == 1, runs

    # -- EC10 dependency failure --------------------------------------------

    def test_ec10_a_missing_scope_is_401_and_not_a_crash(self):
        """MEASURED: this template answers 401, not 403.

        `app_factory` maps `Unauthorized` -> 401 and `PolicyRefused` -> 403, and
        a caller whose scopes do not admit the route raises the first. The two
        are different questions -- "I do not know who you are" versus "I know,
        and no" -- so a service that resolves a principal and then refuses it on
        POLICY should be answering 403 and will need this assertion changed.
        Stated rather than assumed, because the code that surprised the author
        is the code that surprises the next reader.
        """
        response = self.client.get("/widgets", headers=headers(scopes="widgets:none"))
        assert response.status_code == 401

    def test_ec10_the_same_call_with_the_right_scope_succeeds(self):
        """The control. Auth that refused EVERY caller passes the test above."""
        assert self.list_widgets().status_code == 200

    # -- EC11 concurrency ---------------------------------------------------

    def test_ec11_interleaved_writes_from_two_orgs_do_not_contaminate(self):
        mine = self.client.post("/widgets", json={"name": "Mine"}, headers=headers()).json()
        theirs = self.client.post(
            "/widgets", json={"name": "Theirs"}, headers=headers(OTHER_ORG)
        ).json()
        assert self.get_widget(mine["widget_id"]).status_code == 200
        assert self.get_widget(theirs["widget_id"], org=OTHER_ORG).status_code == 200
        assert self.get_widget(theirs["widget_id"], org=ORG).status_code == 404

    # EC12 N/A: `GET /widgets` returns the org's whole set with no cursor, page
    # or offset, so there is no page boundary to walk off. A service that adds
    # pagination must delete this waiver and cover the last page, a cursor past
    # the end, and a tampered cursor.

    def test_the_listing_is_unpaged_and_offers_no_cursor(self):
        body = self.list_widgets().json()
        assert "cursor" not in body and "next" not in body

    # -- EC13 numeric and temporal edges ------------------------------------

    def test_ec13_created_at_is_the_pinned_clock_and_not_wall_time(self):
        created = self.client.post("/widgets", json={"name": "Timed"}, headers=headers())
        assert created.json()["created_at"].startswith("2026-01-01")

    # -- EC14 null vs absent vs empty ---------------------------------------

    def test_ec14_an_empty_name_is_refused_rather_than_stored(self):
        response = self.client.post("/widgets", json={"name": ""}, headers=headers())
        assert response.status_code == 422

    def test_ec14_an_explicit_null_name_is_refused_not_defaulted(self):
        response = self.client.post("/widgets", json={"name": None}, headers=headers())
        assert response.status_code == 422
