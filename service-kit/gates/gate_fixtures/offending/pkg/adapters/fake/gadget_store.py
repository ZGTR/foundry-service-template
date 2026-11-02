from pkg.domain import Gadget


class DictGadgetStore:
    def __init__(self) -> None:
        self._rows: dict[tuple[str, str], Gadget] = {}

    def put(self, gadget: Gadget) -> None:
        self._rows[(gadget.tenant_id, gadget.gadget_id)] = gadget
