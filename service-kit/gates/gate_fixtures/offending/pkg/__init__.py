"""An OFFENDING service: ONE deliberate violation per CLAUSE, each marked.

Every planted violation carries the marker token that
`scripts/gate_common.py::MARKER` names -- this file deliberately does not quote
it, because a file that merely MENTIONS the token would be counted as one that
carries a planted violation, and the gates' marker count would be measuring
prose. (`.claude/CLAUDE.md` records the same shape from the other side: an
environment check that flagged the port docstring whose whole purpose was to say
"do not read os.environ here".)

Stripping the token from any planted file turns every gate RED -- that is the
arm each gate runs before it is allowed to issue a verdict on real source.

One offender per CLAUSE, never one per gate. With two offenders in one file a
gate can redden for the WRONG clause and the arm proves nothing, which is
indistinguishable from a working control.
"""
