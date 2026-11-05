"""§6: a rollback-safe, IDEMPOTENT startup path.

`start()` may be called any number of times and does its work once. That is not
tidiness: an ASGI server with a reload worker, a Lambda Web Adapter restarting a
cold container, and a test client entering the app twice all call the startup
hook more than once, and a second migration run or a second signal handler
install is how a green deployment becomes a broken one.

Rollback-safe means a FAILED start leaves nothing half-installed: the startup
latch is only set after every step has succeeded, so a process that failed to
migrate reports `/health/startup` red and is never routed traffic, rather than
serving from a half-migrated database.
"""

from __future__ import annotations

import logging
import threading

from foundry_data_gateway.runtime.logging import configure_json_logging
from foundry_data_gateway.runtime.migrations import MigrationOutcome, MigrationRunner
from foundry_data_gateway.runtime.probes import RuntimeHealthReporter
from foundry_data_gateway.runtime.shutdown import GracefulShutdown

__all__ = ["ServiceLifecycle"]

_LOGGER = logging.getLogger("foundry_data_gateway.runtime")


class ServiceLifecycle:
    def __init__(
        self,
        *,
        reporter: RuntimeHealthReporter,
        shutdown: GracefulShutdown,
        migrations: MigrationRunner | None = None,
        log_level: str = "INFO",
        install_signal_handlers: bool = True,
    ) -> None:
        self._reporter = reporter
        self._shutdown = shutdown
        self._migrations = migrations
        self._log_level = log_level
        self._install_signal_handlers = install_signal_handlers
        self._lock = threading.Lock()
        self._started = False
        self.migration_outcome: MigrationOutcome | None = None

    @property
    def started(self) -> bool:
        return self._started

    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            configure_json_logging(self._log_level)
            if self._migrations is not None:
                self.migration_outcome = self._migrations.run()
            if self._install_signal_handlers:
                self._shutdown.install()
            # LAST. Everything above must have succeeded for startup to latch;
            # a failure propagates with the latch still false, so readiness
            # stays red and no traffic reaches a half-started process.
            self._reporter.mark_started()
            self._started = True
            _LOGGER.info("service started", extra={"event": "startup.complete"})

    def stop(self, timeout: float | None = None) -> bool:
        """Drain and report whether it completed. Idempotent."""
        self._shutdown.request_shutdown()
        drained = self._shutdown.drain(timeout)
        _LOGGER.info(
            "service stopped",
            extra={"event": "shutdown.complete", "drained": drained},
        )
        self._shutdown.restore()
        return drained
