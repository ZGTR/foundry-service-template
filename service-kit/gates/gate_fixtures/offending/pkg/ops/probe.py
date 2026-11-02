"""GATE_MUST_FLAG_THIS -- an undeclared top-level layer that reaches straight into
inner layers, with neither gate noticing.

Reproduces an earlier change: a service adds `ops/` under `src/<pkg>/` with no
entry in `package_roles.yaml`. Before this fix neither `check_layering.py`
(Sec1's direction matrix, ALLOWED) nor `check_folder_structure.py` (Sec4's closed
top-level set) recognised anything outside the seven declared layers -- domain,
ports, application, adapters, transport, runtime, composition -- so an eighth
directory was invisible to both: `check_layering` skipped its direction check
entirely (`ALLOWED.get("ops")` is `None`) and `check_folder_structure` had no
closed-set clause at this level at all. This file must trip exactly ONE new
finding -- rule 5, "unknown top-level layer" -- and nothing else: the imports
below deliberately target a PACKAGE (`transport.http.controllers`,
`adapters.real`), never a leaf module, so rule 4's deep-import clause stays
quiet and this fixture proves only the clause it exists for.
"""

import pkg.adapters.real as _real_adapters  # noqa: F401
import pkg.transport.http.controllers as _controllers  # noqa: F401
