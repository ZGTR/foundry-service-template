"""`adapters.real` -- implementations that talk to actual infrastructure.

THE PUBLIC CONTRACT of this sub-package, published LAZILY through PEP 562's
module `__getattr__`. The names are declared in `_MODULE_BY_NAME` -- a reader
sees the whole surface in one place, and `scripts/check_layering.py` accepts a
cross-submodule import of `adapters.real` because it resolves to a package with
a declared contract.

WHY LAZY, stated rather than discovered later. Eagerly re-exporting these would
import `boto3` at package-import time, which defeats the import guard this
package pays for (`tests/unit/test_sqs_event_bus_import_guard.py`): it asserts
that a service without the `aws` extra installed still imports, and refuses at
CONSTRUCTION with a `ConfigurationError` rather than leaking a vendor
`ImportError` from an unrelated code path. A module `__getattr__` keeps the
contract explicit AND keeps the import cost on the branch that actually selects
the adapter.

`sqlalchemy` needs no such deferral and gets none: persistence is this
service's reason to exist, so the ORM is a hard dependency rather than an
extra, and the SQL names below import it eagerly through their own modules.
The DBAPI DRIVER is still chosen late -- by the dialect in `DATABASE_URL`, at
first connect -- which is why `engine_factory.py` imports no driver at all.
"""

from __future__ import annotations

import importlib
from typing import Any

_MODULE_BY_NAME = {
    "AlembicMigrations": "alembic_migrations",
    "AwsSecretsManagerSecrets": "aws_secrets_manager",
    "MySqlSource": "sql_sources",
    "PostgresRlsSource": "sql_sources",
    "S3CatalogueLoader": "yaml_catalogue",
    "SqlReceiptStore": "sql_receipts",
    "SqlSource": "sql_sources",
    "SqlSourceRegistry": "sql_sources",
    "SqlUsageStore": "sql_receipts",
    "YamlCatalogueLoader": "yaml_catalogue",
    "InProcessEventBus": "in_process_event_bus",
    "JwksKeyResolver": "jwt_principal_auth",
    "JwtPrincipalAuth": "jwt_principal_auth",
    "OutboxEventBus": "outbox_event_bus",
    "RepositoryReadinessCheck": "readiness",
    "RequestTransaction": "serialized_transaction",
    "SqlUnitOfWork": "sql_unit_of_work",
    "SqlWidgetRepository": "sql_widget_repository",
    "SqsPublisher": "sqs_event_bus",  # COMMS-FLAVOUR-STRIP-LINE
    "build_engine": "engine_factory",
}

__all__ = sorted(_MODULE_BY_NAME)


def __getattr__(name: str) -> Any:
    try:
        module_name = _MODULE_BY_NAME[name]
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
    module = importlib.import_module(f"{__name__}.{module_name}")
    return getattr(module, name)


def __dir__() -> list[str]:
    return __all__
