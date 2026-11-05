"""A CONFORMING controller whose presenter is not named after it.

This is the control for the view-by-import clause, and it is deliberately not a
violation: a sprocket IS a gadget on the wire, so it presents through
`GadgetView` rather than through a duplicate `sprockets_view.py` written only to
satisfy a filename rule. Deriving the view path from the controller stem reports
this file as missing a view -- a false red naming a real file -- which is what
resolving the view by IMPORT fixes.

Blinding `_imported_view_names` turns the CONFORMING arm red on this file, which
is the property that makes it a control rather than decoration.
"""

from fastapi import APIRouter

from pkg.application import RegisterGadget
from pkg.transport.http.schemas.sprockets_schema import SprocketPayload
from pkg.transport.http.views import GadgetView


def build_router(register: RegisterGadget) -> APIRouter:
    router = APIRouter(prefix="/sprockets")

    @router.post("")
    def post_sprocket(payload: SprocketPayload) -> dict[str, object]:
        gadget = register.execute(
            gadget_id=payload.gadget_id, tenant_id=payload.tenant_id, label=payload.label
        )
        return GadgetView.single(gadget)

    return router
