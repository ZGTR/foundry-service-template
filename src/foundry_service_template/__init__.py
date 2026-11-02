"""foundry-service-template: a reference service, deployable to Lambda or ECS from one image.

THE PACKAGE ROOT, and the reference implementation of
the service module contract (README.md, "Service module contract") (v2). The seven physical
submodules below ARE the layers -- a boundary is a directory a reader can see,
not a mapping only a gate can resolve:

    domain/       invariants, values, entities, policy, domain events and errors
    ports/        consumer-owned interfaces, one per file
    application/  use cases, one per file; sequencing only
    adapters/     concrete port implementations, real/ and fake/
    transport/    MVC view + controller, per inbound protocol
    composition/  the composition root: the ONLY place concretes are constructed
    runtime/      production lifecycle: startup, shutdown, signals, health

Each submodule's `__init__.py` is its PUBLIC CONTRACT. Import across submodules
through it (`from foundry_service_template.domain import Widget`), never
through a deep path -- `scripts/check_layering.py` fails the build on one.

This root re-exports NOTHING on purpose. A root that re-exported the whole
package would import every submodule, including the vendor-backed adapters, the
moment anything imported anything -- which is the cost `adapters/real/`'s lazy
contract exists to avoid.
"""

from __future__ import annotations

__all__: list[str] = []
