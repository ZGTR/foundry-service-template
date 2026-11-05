# GATE_MUST_FLAG_THIS -- clause: check_untimed_waits, U3 across files. Nothing in
# this file says `_ready` is a lock: `test_waits_forever.py` binds it, in `Gate`.
# Read alone this file is quiet. Read with that one it has one finding, which is
# the shape the gate was written for: the class that makes the lock lives in a
# support module and the test that takes it lives somewhere else.


class Latecomer:
    def __init__(self, gate: object) -> None:
        self._gate = gate

    def wait_for_the_gate(self) -> None:
        with self._gate._ready:
            pass
