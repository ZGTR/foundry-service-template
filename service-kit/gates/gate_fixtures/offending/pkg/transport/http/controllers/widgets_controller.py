"""GATE_MUST_FLAG_THIS -- the view it presents through names no inner type.

Four files exist for `widgets`, so the only thing wrong here is the MODEL LINK:
`widgets_view.py` claims a domain type in prose and imports none. One fixture
per CLAUSE -- this file must produce exactly one finding, and it must be the
model-link one, or the arm would be red for a reason that has nothing to do with
the clause it was built for.
"""

from fastapi import APIRouter

from pkg.transport.http.schemas.widgets_schema import WidgetPayload
from pkg.transport.http.views.widgets_view import WidgetView


def build_router() -> APIRouter:
    router = APIRouter(prefix="/widgets")

    @router.post("")
    def post_widget(payload: WidgetPayload) -> dict[str, object]:
        return WidgetView.single({"widget_id": payload.widget_id})

    return router
