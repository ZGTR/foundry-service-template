# THE CONTROL FOR check_layering's LAYER_FRAMEWORK_ALLOWANCES, direction A: an
# `application` module importing `pydantic` is ACCEPTED. This file is NOT marked
# GATE_MUST_FLAG_THIS -- it is a CONFORMING file and the gate must stay quiet on it.
# Blinding the allowance in check_layering turns this import into a rule-2 finding and the
# conforming arm goes red, which is what makes the clause load-bearing rather than
# decorative. Owner ruling.
#
# The import is USED, not merely present. An unused import is what a formatter deletes,
# and a control a tool can remove without anyone noticing is not a control.
#
# `execute`'s signature is deliberately UNCHANGED. Four other fixture files name this class
# and one of them calls it; a fixture edit that rewrites a shared contract is measuring the
# edit rather than the clause.
from pydantic import BaseModel

from pkg.domain import Gadget
from pkg.ports import GadgetStore


class RegisterGadgetCommand(BaseModel):
    """The use case's own input contract, validated at the application boundary."""

    gadget_id: str
    tenant_id: str
    label: str


class RegisterGadget:
    def __init__(self, store: GadgetStore) -> None:
        self._store = store

    def execute(self, *, gadget_id: str, tenant_id: str, label: str) -> Gadget:
        command = RegisterGadgetCommand(
            gadget_id=gadget_id, tenant_id=tenant_id, label=label
        )
        gadget = Gadget(
            gadget_id=command.gadget_id,
            tenant_id=command.tenant_id,
            label=command.label,
        )
        self._store.put(gadget)
        return gadget
