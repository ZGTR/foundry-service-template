# GATE_MUST_FLAG_THIS -- two clauses that are NOT this gate's:
#   * check_size_limits: `list_doodads` is a route handler over the 12-line
#     ceiling.
#   * check_layering rule 4: the module reaches through `pkg.domain.model.gadget`,
#     a deep path that bypasses the domain submodule's public contract.
#
# It is correctly FOUR-FILED (view + schema + controller + model), and every
# handler delegates its shaping to the view, so check_mvc_separation stays quiet
# here. That silence is load-bearing: it is what makes the MVC arm next door a
# measurement of the MVC clause rather than of "some controller is wrong".
from fastapi import APIRouter

from pkg.domain.model.gadget import Gadget
from pkg.transport.http.schemas import GadgetPayload
from pkg.transport.http.views import DoodadView


def build_router() -> APIRouter:
    router = APIRouter(prefix="/doodads")

    @router.post("")
    def post_doodad(payload: GadgetPayload) -> dict[str, object]:
        doodad = Gadget(
            gadget_id=payload.gadget_id, tenant_id=payload.tenant_id, label=payload.label
        )
        return DoodadView.single(doodad)

    @router.get("")
    def list_doodads() -> dict[str, object]:
        first = Gadget(gadget_id="1", tenant_id="t", label="a")
        second = Gadget(gadget_id="2", tenant_id="t", label="b")
        third = Gadget(gadget_id="3", tenant_id="t", label="c")
        fourth = Gadget(gadget_id="4", tenant_id="t", label="d")
        fifth = Gadget(gadget_id="5", tenant_id="t", label="e")
        sixth = Gadget(gadget_id="6", tenant_id="t", label="f")
        seventh = Gadget(gadget_id="7", tenant_id="t", label="g")
        eighth = Gadget(gadget_id="8", tenant_id="t", label="h")
        ninth = Gadget(gadget_id="9", tenant_id="t", label="i")
        every = (first, second, third, fourth, fifth, sixth, seventh, eighth, ninth)
        return DoodadView.collection(every)

    return router
