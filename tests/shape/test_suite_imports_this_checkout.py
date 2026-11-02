"""The suite must test THIS checkout's source, and nothing asserted that before.

WHAT WENT WRONG, so the test can be read against it. `[tool.pytest.ini_options]
pythonpath` listed `service-kit/testing`, `service-kit/vendor` and `"."`, and not
`src`. So `import foundry_service_template` resolved through whatever pip had
installed. In CI that is this same tree (`pip install --no-deps -e .`) and the
arrangement is correct; in a virtualenv shared with a sibling worktree it is the
SIBLING's tree, because setuptools' editable install drops a `.pth` naming an
absolute path.

Measured 2026-09-18 in this repository: a shared venv carried
`_editable_impl_foundry_service_template.pth` pointing at another checkout, and
this suite ran 328 tests against that checkout's source while reporting on this
one. It surfaced as `TypeError: build_container() got an unexpected keyword
argument 'database_engine'` for a parameter sitting in this checkout's own file --
which reads as an editing mistake and sends the reader to the one place the fault
is not. Every green test in that run had measured a different tree.

WHY A TEST AND NOT A COMMENT. The fix is one entry in `pythonpath`, and an entry
in a config file is exactly the kind of thing a later edit removes while
rearranging a list. Nothing else in this suite can notice: every other test would
go on passing, against the wrong source, for as long as the two trees happen to
agree.
"""

from __future__ import annotations

from pathlib import Path

import foundry_service_template

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
EXPECTED_SRC = REPOSITORY_ROOT / "src"


def test_the_package_under_test_is_this_checkouts_source() -> None:
    imported = Path(foundry_service_template.__file__).resolve()

    assert imported.is_relative_to(EXPECTED_SRC), (
        f"this suite imported {imported}, which is not under {EXPECTED_SRC}. The tests "
        f"are measuring a different checkout's source. Add 'src' to "
        f"[tool.pytest.ini_options] pythonpath in pyproject.toml, or remove the editable "
        f"install pointing elsewhere."
    )


def test_the_expected_source_directory_actually_exists() -> None:
    """The positive control.

    `is_relative_to` against a path that does not exist is simply False, so an
    EXPECTED_SRC computed with the wrong number of `parents` would turn the arm
    above into a permanent red naming a real directory -- the false-red direction,
    where the natural response is to go fix the import.
    """
    assert (EXPECTED_SRC / "foundry_service_template" / "__init__.py").is_file()
