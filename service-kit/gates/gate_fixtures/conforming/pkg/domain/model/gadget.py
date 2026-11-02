from dataclasses import dataclass


@dataclass(frozen=True)
class Gadget:
    gadget_id: str
    tenant_id: str
    label: str
