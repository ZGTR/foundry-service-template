from collections.abc import Iterable

from pkg.domain import Gadget


class DoodadView:
    @staticmethod
    def single(doodad: Gadget) -> dict[str, object]:
        return {
            "gadget_id": doodad.gadget_id,
            "tenant_id": doodad.tenant_id,
            "label": doodad.label,
        }

    @classmethod
    def collection(cls, doodads: Iterable[Gadget]) -> dict[str, object]:
        return {"doodads": [cls.single(doodad) for doodad in doodads]}
