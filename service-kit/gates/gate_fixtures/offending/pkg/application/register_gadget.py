# GATE_MUST_FLAG_THIS -- clause: check_layering rule 1, DIRECTION. `application`
# imports `adapters`, so the use case has made the wiring decision that belongs
# to exactly one module and can no longer be tested against a fake.
from pkg.adapters.fake import DictGadgetStore
from pkg.domain import Gadget


class RegisterGadget:
    def __init__(self) -> None:
        self._store = DictGadgetStore()

    def execute(self, *, gadget_id: str, tenant_id: str, label: str) -> Gadget:
        gadget = Gadget(gadget_id=gadget_id, tenant_id=tenant_id, label=label)
        self._store.put(gadget)
        return gadget
