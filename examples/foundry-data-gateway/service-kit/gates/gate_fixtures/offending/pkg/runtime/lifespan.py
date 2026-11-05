# GATE_MUST_FLAG_THIS -- clause: check_lifecycle. This runtime declares NO
# startup probe, NO signal handling, NO structured logging and NO migration
# application. It looks like a lifecycle module and provides none of §6's
# guarantees, which is the shape that reads as covered in review.


class BootSequence:
    def __init__(self) -> None:
        self._booted = False

    def boot(self) -> None:
        self._booted = True
