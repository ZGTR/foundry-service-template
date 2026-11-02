"""§6: the three probe endpoints, plus the two legacy aliases.

These live in `runtime/` rather than `transport/http/controllers/` on purpose.
A probe is not an API resource -- it has no domain model, no use case and no
presenter, so MVC's four-file rule (§2) does not apply to it, and putting it
under `controllers/` would make every MVC gate demand a `health` model and a
`health` view that should not exist. `/healthz` and `/readyz` are kept as
aliases because deployed task definitions and ALB target groups already point
at them; dropping them would be a silent 404 at the exact moment a rollout
needs the probe to answer.
"""

from __future__ import annotations

from fastapi import APIRouter, Response, status

from foundry_service_template.ports import HealthReport, HealthReporter

__all__ = ["build_router"]


def _respond(report: HealthReport, response: Response) -> dict[str, object]:
    response.status_code = (
        status.HTTP_200_OK if report.healthy else status.HTTP_503_SERVICE_UNAVAILABLE
    )
    return report.as_data()


def build_router(reporter: HealthReporter) -> APIRouter:
    router = APIRouter(tags=["health"])

    @router.get("/health/live")
    def live(response: Response) -> dict[str, object]:
        return _respond(reporter.live(), response)

    @router.get("/health/ready")
    def ready(response: Response) -> dict[str, object]:
        return _respond(reporter.ready(), response)

    @router.get("/health/startup")
    def startup(response: Response) -> dict[str, object]:
        return _respond(reporter.startup(), response)

    @router.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "up"}

    @router.get("/readyz")
    def readyz(response: Response) -> dict[str, bool]:
        report = reporter.ready()
        response.status_code = (
            status.HTTP_200_OK if report.healthy else status.HTTP_503_SERVICE_UNAVAILABLE
        )
        return {"ready": report.healthy}

    return router
