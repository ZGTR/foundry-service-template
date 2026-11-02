from typing import Protocol

from pkg.domain import Gadget


class GadgetStore(Protocol):
    def put(self, gadget: Gadget) -> None: ...
