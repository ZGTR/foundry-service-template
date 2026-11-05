"""`S3CatalogueLoader` against a real S3 API (the kit's LocalStack): the deployed path."""

from __future__ import annotations

import uuid
from pathlib import Path

from foundry_data_gateway.adapters.real import S3CatalogueLoader

ROOT = Path(__file__).resolve().parents[2]


def test_the_s3_catalogue_loads_the_same_packs_and_policy_as_the_directory(aws_kit) -> None:
    s3, bucket = aws_kit.client("s3"), f"fdg-packs-{uuid.uuid4().hex[:8]}"
    s3.create_bucket(Bucket=bucket)
    for pack in sorted((ROOT / "packs").glob("*.tools.yaml")):
        s3.put_object(Bucket=bucket, Key=f"catalogue/{pack.name}", Body=pack.read_bytes())
    policy = (ROOT / "e2e/fixtures/policy.yaml").read_bytes()
    s3.put_object(Bucket=bucket, Key="catalogue/policy.yaml", Body=policy)
    loaded = S3CatalogueLoader(f"s3://{bucket}/catalogue/", s3).load()
    assert set(loaded.packs) == {"alphazed_learning", "shepherd_operations"}
    assert [t.name for t in loaded.granted_tools("org_globex")] == [
        "spend_by_employee",
        "model_usage",
    ]
