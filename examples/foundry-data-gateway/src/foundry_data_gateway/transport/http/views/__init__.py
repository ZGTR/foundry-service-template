"""MVC "View": presenters. A view turns a domain result into PLAIN DATA.

A presenter returns `dict`/`list`/scalars -- never a framework response, never
a domain object. Two consequences the contract cares about: a controller stays
a route plus a delegation (§2's 12-line ceiling), and the response SHAPE is
testable without an HTTP client, a running app or a serialiser.
"""

from __future__ import annotations

from foundry_data_gateway.transport.http.views.widgets_view import WidgetView

__all__ = ["WidgetView"]
