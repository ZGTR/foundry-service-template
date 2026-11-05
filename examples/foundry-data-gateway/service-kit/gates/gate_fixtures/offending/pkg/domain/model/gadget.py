# GATE_MUST_FLAG_THIS -- clause: check_layering rule 2, a framework import in
# the DOMAIN. `fastapi` in a domain model makes the innermost layer untestable
# without a web framework, which is the whole reason the layer exists.
from dataclasses import dataclass

from fastapi import Request  # noqa: F401


@dataclass(frozen=True)
class Gadget:
    gadget_id: str
    tenant_id: str
    label: str
