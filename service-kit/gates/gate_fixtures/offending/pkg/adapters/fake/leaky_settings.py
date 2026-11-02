# GATE_MUST_FLAG_THIS -- clause: check_layering rule 3, the environment is read
# outside composition/config.py. An adapter that resolves its own configuration
# means a local run and a production run differ by a branch inside every
# consumer rather than by one constructor argument.
import os


def bucket_name() -> str:
    return os.environ.get("GADGET_BUCKET", "gadgets")
