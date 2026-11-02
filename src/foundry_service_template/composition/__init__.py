"""`composition` -- the composition root.

THE PUBLIC CONTRACT of this submodule, and the ONLY place in the package where
a concrete adapter is constructed or the process environment is read.
`config.py` owns the environment; `wiring.py` picks concretes per configuration
axis; `container.py` assembles the graph; `app_factory.py` builds the ASGI app
and attaches the runtime lifecycle; `main.py` is the process entrypoint.

`composition` sits at the top of §1's direction, so it may import every other
submodule. Nothing may import IT -- an application, transport or runtime module
that reaches back into the composition root has turned the container into a
service locator, which §3 forbids outright.
"""

from __future__ import annotations

from foundry_service_template.composition.app_factory import create_app
from foundry_service_template.composition.config import ServiceConfig
from foundry_service_template.composition.container import (
    LOCAL_ENVS,
    Container,
    InsecureDefaultAuth,
    build_container,
)

__all__ = [
    "LOCAL_ENVS",
    "Container",
    "InsecureDefaultAuth",
    "ServiceConfig",
    "build_container",
    "create_app",
]
