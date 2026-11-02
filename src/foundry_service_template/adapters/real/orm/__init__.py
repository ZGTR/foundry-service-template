"""`adapters.real.orm` -- the table mappings, and nothing else.

ADAPTER-PRIVATE BY CONTRACT. A record class is the shape of a ROW, and a row's
shape is the storage adapter's business; letting one escape into `application/`
or `transport/` means the ORM's identity map, lazy loading and mutation
semantics reach code that was written against a frozen value object. §1's
dependency direction already forbids the inner layers from importing
`adapters`, and `service-kit/gates/check_layering.py` names `sqlalchemy` among
the framework roots the inner layers may not mention, so the two halves of that
rule are enforced from both ends.

Eagerly re-exported, unlike `adapters.real` itself: `sqlalchemy` is a hard
dependency of this service rather than an extra, so there is no import to
defer and nothing here can fail to import on a checkout that omitted a driver.

`migrations/env.py` imports `Base` FROM HERE, and every record module with it,
because a record class alembic never imports is a table `Base.metadata` never
learns about -- autogenerate then reports the schema as up to date while the
table is missing.
"""

from __future__ import annotations

from foundry_service_template.adapters.real.orm.base import NAMING_CONVENTION, Base, as_utc
from foundry_service_template.adapters.real.orm.outbox_record import OutboxRecord
from foundry_service_template.adapters.real.orm.widget_record import WidgetRecord

__all__ = ["NAMING_CONVENTION", "Base", "OutboxRecord", "WidgetRecord", "as_utc"]
