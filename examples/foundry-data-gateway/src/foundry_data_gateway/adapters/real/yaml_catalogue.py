"""Tool packs and the policy, read as YAML from a directory or from S3.

Every `*.tools.yaml` is one pack; `policy.yaml` beside them is the tenant policy. Locally
the source is a directory (`TOOL_PACKS_DIR` + `TOOL_POLICY_FILE`); a deployment points
`TOOL_CATALOGUE_URI` at `s3://bucket/prefix/`, where IaC uploads the same files, so a
pack change is a reviewed object version, not an image rebuild. Loading validates
everything, so a broken pack fails the process at startup, not at the first agent call.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from foundry_data_gateway.domain import InvalidToolPack, ToolPolicy, merge_packs, parse_pack


def _build(pack_texts: list[str], policy_text: str) -> ToolPolicy:
    if not pack_texts:
        raise InvalidToolPack("no *.tools.yaml packs found")
    packs = merge_packs([parse_pack(yaml.safe_load(t)) for t in pack_texts])
    return ToolPolicy.parse(yaml.safe_load(policy_text), packs)


class YamlCatalogueLoader:
    def __init__(self, packs_dir: str, policy_file: str) -> None:
        self._packs_dir = Path(packs_dir)
        self._policy_file = Path(policy_file)

    def load(self) -> ToolPolicy:
        files = sorted(self._packs_dir.glob("*.tools.yaml"))
        return _build([f.read_text() for f in files], self._policy_file.read_text())


class S3CatalogueLoader:
    """`s3://bucket/prefix/`: every `*.tools.yaml` under the prefix plus `policy.yaml`.

    Read once and cached for the process; a new pack version takes effect on the next
    deployment (rolling restart), which keeps "which catalogue served this call" equal to
    the receipt's policy version for the life of a task.
    """

    def __init__(self, uri: str, client: object) -> None:
        bucket, _, prefix = uri.removeprefix("s3://").partition("/")
        self._bucket, self._prefix, self._s3 = bucket, prefix, client
        self._cached: ToolPolicy | None = None

    def _text(self, key: str) -> str:
        body = self._s3.get_object(Bucket=self._bucket, Key=key)["Body"]  # type: ignore[attr-defined]
        return body.read().decode("utf-8")

    def load(self) -> ToolPolicy:
        if self._cached is None:
            listing = self._s3.list_objects_v2(Bucket=self._bucket, Prefix=self._prefix)  # type: ignore[attr-defined]
            keys = sorted(
                o["Key"] for o in listing.get("Contents", []) if o["Key"].endswith(".tools.yaml")
            )
            self._cached = _build(
                [self._text(k) for k in keys], self._text(self._prefix + "policy.yaml")
            )
        return self._cached
