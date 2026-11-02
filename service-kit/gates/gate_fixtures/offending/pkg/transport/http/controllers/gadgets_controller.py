# GATE_MUST_FLAG_THIS -- clause: check_mvc_separation, BOTH halves, isolated to
# this resource so no other gate can redden here:
#   * the `gadgets` resource has NO view file; and
#   * `post_gadget` shapes its response inline instead of delegating to one.
#
# Every OTHER deliberate violation lives on the `doodads` resource next door,
# which is correctly four-filed. One fixture per CLAUSE: with the size-limit and
# deep-import offenders in THIS file, this gate reddened on them too, and an arm
# that goes red for the wrong clause is indistinguishable from a working one.
from fastapi import APIRouter

from pkg.application import RegisterGadget
from pkg.transport.http.schemas import GadgetPayload


def build_router(register: RegisterGadget) -> APIRouter:
    router = APIRouter(prefix="/gadgets")

    @router.post("")
    def post_gadget(payload: GadgetPayload) -> dict[str, object]:
        gadget = register.execute(
            gadget_id=payload.gadget_id, tenant_id=payload.tenant_id, label=payload.label
        )
        return {
            "gadget_id": gadget.gadget_id,
            "tenant_id": gadget.tenant_id,
            "label": gadget.label,
        }

    return router
