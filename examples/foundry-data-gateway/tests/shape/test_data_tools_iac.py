"""The data-tool IaC injects only settings the service actually reads, and the packs it
publishes are the ones the repository ships."""

from __future__ import annotations

from pathlib import Path

import hcl2

from foundry_data_gateway.composition.config import ServiceConfig

IAC = Path(__file__).resolve().parents[2] / "deploy" / "iac" / "app"


def _unquote(value: str) -> str:
    return value.strip('"')


def _locals() -> dict:
    merged: dict = {}
    for block in hcl2.load((IAC / "data_tools.tf").open()).get("locals", []):
        merged.update(block)
    return merged


def test_every_injected_variable_is_a_config_field() -> None:
    fields = {name.upper() for name in ServiceConfig.model_fields}
    names = [_unquote(e["name"]) for e in _locals()["data_tool_environment"]]
    names += [_unquote(s["name"]) for s in _locals()["data_tool_secrets"]]
    assert names and set(names) <= fields, sorted(set(names) - fields)


def test_the_task_definition_carries_the_data_tool_env_and_secrets() -> None:
    main = (IAC / "main.tf").read_text()
    assert "local.data_tool_environment" in main and "secrets = local.data_tool_secrets" in main


def test_the_published_pack_directory_is_the_repositorys() -> None:
    variables = {}
    for block in hcl2.load((IAC / "data_tools.tf").open())["variable"]:
        variables.update({_unquote(k): v for k, v in block.items()})
    packs = (IAC / _unquote(variables["tool_packs_dir"]["default"])).resolve()
    assert sorted(p.name for p in packs.glob("*.tools.yaml")) == [
        "alphazed-learning.tools.yaml",
        "shepherd-operations.tools.yaml",
    ]
