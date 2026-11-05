"""Callee URLs under the Foundry service directory's variable names.

The workspace's `scripts/service_directory.yaml` names ONE variable per service a
caller should read: `FOUNDRY_<SERVICE>_URL` (`FOUNDRY_GOVERNANCE_URL`,
`FOUNDRY_LLM_GATEWAY_URL`). `scripts/render_service_urls.py` there renders its value
per run target -- `http://<compose host>:<port>` locally, `https://<service>.<zone>`
everywhere else -- so a service built from this template reads that name and nothing
else for a new callee.

A service that already reads an OLDER name for the same callee keeps working for one
release: the directory variable wins, the legacy one is honoured with a
deprecation warning naming both, and it means exactly what it meant.

Both reads stay literal in `config.py`'s `from_environment()`, the one module that
reads the environment, where the ruff ban and the workspace's env-read census can
see them; this is a pure function over the values it read::

    gateway_url = directory_url(
        "FOUNDRY_LLM_GATEWAY_URL", environ.get("FOUNDRY_LLM_GATEWAY_URL"),
        "LLM_GATEWAY_URL", environ.get("LLM_GATEWAY_URL"),
    )
    if gateway_url is None and needs_gateway:
        raise ValueError(missing_callee_remedy("FOUNDRY_LLM_GATEWAY_URL"))
"""

from __future__ import annotations

import logging

_LOGGER = logging.getLogger(__name__)

#: Where a deployment looks up the variable to set. Named in every refusal that a
#: missing callee URL can cause, because a task definition has no call site to edit.
DIRECTORY_SOURCE = "scripts/service_directory.yaml in the Foundry workspace"


def directory_url(
    directory_var: str,
    directory_value: str | None,
    legacy_var: str,
    legacy_value: str | None,
) -> str | None:
    """`directory_value` when set, else `legacy_value` (with one warning), else None.

    An empty string is unset: a task definition that blanks a variable has not set it.
    """
    if directory_value:
        return directory_value
    if legacy_value:
        _LOGGER.warning(
            "%s is deprecated and is read for one release only; set %s instead "
            "(the service directory's variable, %s)",
            legacy_var,
            directory_var,
            DIRECTORY_SOURCE,
        )
        return legacy_value
    return None


def missing_callee_remedy(directory_var: str) -> str:
    """The refusal text for a REQUIRED callee URL that neither name set.

    It names the variable a deployment can set and where its value comes from --
    never a constructor argument, which a deployment cannot pass.
    """
    return (
        f"{directory_var} is not set. Set it to the callee's URL; its value per run "
        f"target is in {DIRECTORY_SOURCE} (render it with scripts/render_service_urls.py)."
    )
