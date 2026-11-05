"""MVC "Controller": routing and delegation ONLY.

A handler parses, authenticates, calls ONE use case and hands the result to a
view. No business logic, no try/except, no response shaping, 12 lines maximum.
"""

from __future__ import annotations

from foundry_data_gateway.transport.http.controllers import widgets_controller

__all__ = ["widgets_controller"]
