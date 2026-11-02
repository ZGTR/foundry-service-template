"""GATE_MUST_FLAG_THIS -- five unrelated classes in one module.

`check_class_density.py` must flag this file and no other in the offending tree.

The violation is deliberately made of SMALL classes, because that is what makes
this defect distinct from the one `check_size_limits.py` catches: nothing here
is long. No class exceeds its 200-line ceiling, no function exceeds 50 lines,
the module is nowhere near 400. Every length gate passes, and the filename still
predicts none of the five things inside it.

They are also deliberately UNRELATED -- a shape, a colour, a size, a crate and a
label -- rather than variations on one idea. A module holding five variants of
one concept is the case the exception-family exemption exists for, and mixing the
two in one fixture would make this arm red for either reason and prove neither.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DoodadShape:
    name: str


@dataclass(frozen=True)
class DoodadColour:
    hex_value: str


@dataclass(frozen=True)
class DoodadSize:
    millimetres: int


@dataclass(frozen=True)
class DoodadCrate:
    capacity: int


@dataclass(frozen=True)
class DoodadLabel:
    text: str
