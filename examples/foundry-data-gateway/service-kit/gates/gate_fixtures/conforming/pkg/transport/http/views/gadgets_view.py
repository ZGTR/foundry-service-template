from collections.abc import Iterable

from pkg.domain import Gadget


class GadgetView:
    @staticmethod
    def single(gadget: Gadget) -> dict[str, object]:
        return {
            "gadget_id": gadget.gadget_id,
            "tenant_id": gadget.tenant_id,
            "label": gadget.label,
        }

    @classmethod
    def collection(cls, gadgets: Iterable[Gadget]) -> dict[str, object]:
        return {"gadgets": [cls.single(gadget) for gadget in gadgets]}
