"""SQL data sources: one class per scope strategy, one registry keyed by source name.

`SqlSource` owns what every dialect shares (bind the tenant, cap the rows, one read-only
transaction). Subclasses add only what differs:

* `MySqlSource` -- `bound_param`: the tenant is bound into the statement; the session is
  set READ ONLY so even a reviewed statement cannot write.
* `PostgresRlsSource` -- `rls`: the same, plus `SET LOCAL ROLE <reader>` and
  `app.<param>`, so row-level security in the database filters rows a statement bug
  would otherwise return.

The DSN comes from the deployment's connection map (`DATA_SOURCE_URLS`), keyed by the
`connection` name the pack declares, resolved when the source is first used. Engines
are created lazily: building the registry opens nothing.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal

from foundry_contracts.errors import ConfigurationError
from sqlalchemy import Connection, Engine, create_engine, text

from foundry_data_gateway.domain import Source, Tool

EngineFactory = Callable[[str], Engine]


def _plain(value: object) -> object:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return value


class SqlSource:
    def __init__(self, source: Source, engine: Engine) -> None:
        self._source = source
        self._engine = engine

    def read(
        self, tool: Tool, params: Mapping[str, object], *, tenant: str, limit: int
    ) -> Sequence[Mapping[str, object]]:
        bound = {**params, self._source.scope.param: tenant, "_row_limit": limit}
        capped = f"SELECT * FROM ({tool.statement}) AS tool_result LIMIT :_row_limit"
        with self._engine.connect() as conn:
            with conn.begin():
                self._enter_scope(conn, tenant)
                rows = conn.execute(text(capped), bound).mappings().all()
        return [{k: _plain(v) for k, v in row.items()} for row in rows]

    def _enter_scope(self, conn: Connection, tenant: str) -> None:
        """Dialect-specific session setup inside the read transaction."""


class MySqlSource(SqlSource):
    def _enter_scope(self, conn: Connection, tenant: str) -> None:
        # Must precede the transaction's first statement; the begin() above issues none.
        conn.execute(text("SET TRANSACTION READ ONLY"))


class PostgresRlsSource(SqlSource):
    def _enter_scope(self, conn: Connection, tenant: str) -> None:
        conn.execute(text("SET TRANSACTION READ ONLY"))
        conn.execute(text(f'SET LOCAL ROLE "{self._source.reader_role}"'))
        setting = f"app.{self._source.scope.param}"
        conn.execute(text("SELECT set_config(:k, :v, true)"), {"k": setting, "v": tenant})


_CLASS_BY_STRATEGY: dict[tuple[str, str], type[SqlSource]] = {
    ("mysql", "bound_param"): MySqlSource,
    ("postgres", "bound_param"): SqlSource,
    ("postgres", "rls"): PostgresRlsSource,
}


class SqlSourceRegistry:
    """`connections` maps a source's `connection` name to its DSN; injected by the
    composition root from configuration, never read from the environment here."""

    def __init__(
        self, connections: Mapping[str, str], engine_factory: EngineFactory | None = None
    ) -> None:
        self._connections = dict(connections)
        self._engine_factory = engine_factory or (
            lambda url: create_engine(url, pool_pre_ping=True)
        )
        self._built: dict[str, SqlSource] = {}

    def for_source(self, source: Source) -> SqlSource:
        if source.name not in self._built:
            url = self._connections.get(source.connection)
            if not url:
                raise ConfigurationError(
                    f"source {source.name} needs DATA_SOURCE_URLS[{source.connection!r}], "
                    "which is unset"
                )
            cls = _CLASS_BY_STRATEGY[(source.kind, source.scope.strategy)]
            self._built[source.name] = cls(source, self._engine_factory(url))
        return self._built[source.name]
