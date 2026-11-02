"""An EMPTY `MetaData`, so the kit's vacuous-truncate refusal has a real subject.

`foundry_testkit.real_profile.owned_tables` refuses a mapping that declares no
tables, because `TRUNCATE` over nothing succeeds, isolates nothing, and reads as a
working harness. That clause needs a fixture whose `sorted_tables` is genuinely
empty and genuinely a list.

`sqlalchemy:MetaData` is the obvious candidate and is the WRONG one: it is the
class, so `sorted_tables` is an unbound property and iterating it raises
`TypeError` from a different clause entirely. An arm written against it is red for
the wrong reason, which reads exactly like a working control. That near miss has
its own arm; this module is the one for the empty mapping.

It lives under `tests/support/` rather than in the kit because it is a FIXTURE --
service-owned material a `sync.py` run must never overwrite.
"""

from __future__ import annotations

from sqlalchemy import MetaData

#: Declared here and nowhere else. A record class imported into this module would
#: silently give it a table and turn the control green for a reason nobody chose.
EMPTY_METADATA = MetaData()
