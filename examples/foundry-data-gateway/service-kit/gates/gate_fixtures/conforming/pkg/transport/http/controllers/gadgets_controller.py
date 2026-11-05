from fastapi import APIRouter

from pkg.application import RegisterGadget
from pkg.transport.http.schemas import GadgetPayload
from pkg.transport.http.views import GadgetView


def build_router(register: RegisterGadget) -> APIRouter:
    router = APIRouter(prefix="/gadgets")

    @router.post("")
    def post_gadget(payload: GadgetPayload) -> dict[str, object]:
        gadget = register.execute(
            gadget_id=payload.gadget_id, tenant_id=payload.tenant_id, label=payload.label
        )
        return GadgetView.single(gadget)

    return router
