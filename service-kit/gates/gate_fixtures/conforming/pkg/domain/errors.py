"""The exception family: six classes in one module, and correct.

This fixture carries NO marker. It is the control for
`check_class_density.py`'s structural exemption, and it is over the ceiling on
purpose -- six top-level classes against a limit of four. An exception family
that stayed UNDER the limit would make the conforming arm green whether the
exemption works or not, which is why `EXPECTED_CONFORMING_EXEMPTIONS` asserts
the run actually exempted something rather than merely finding nothing.

Splitting this into six files would satisfy a flat class count and make the code
worse: these are one idea at six values, the base is meaningless alone, and a
caller writing `except GadgetError` wants them in one place.

The inheritance is deliberately INDIRECT for two of them -- `GadgetRejected`
derives from `GadgetInvalid`, not from `GadgetError` -- because the realistic
shape is a small hierarchy, and a check that only recognised a direct builtin
base would exempt the base and flag its children. The exemption resolves
membership transitively within the module, and this is what proves it.
"""

from __future__ import annotations


class GadgetError(Exception):
    """The family's base. Never raised directly."""


class GadgetNotFound(GadgetError):
    pass


class GadgetInvalid(GadgetError):
    pass


class GadgetRejected(GadgetInvalid):
    """Two hops from Exception, and the reason this fixture is not trivial."""


class GadgetConflict(GadgetError):
    pass


class GadgetExpired(GadgetRejected):
    """Three hops. The fixed-point resolution has to reach this one."""
