"""The test kit: the seeding kit, the AWS kit and the cache kit.

These exist so that §7 -- "minimal mocks" -- is a workable rule rather than an
aspiration. `unittest.mock` is easy because the alternative is usually
unwritten; these are the alternative, written once.

Everything here reaches a REAL resource or a real in-memory adapter. Where the
real resource is genuinely unavailable, the kit raises `ResourceUnavailable`,
which the fixtures turn into a NAMED skip that says what was missing and how to
provide it -- never a silent pass, and never a mock standing in.

It lives in `service-kit/` and is imported as `foundry_testkit`, not as
`tests.support`, and the rename is the point rather than tidiness: these modules
are byte-identical in every service, so a name rooted in one service's `tests/`
tree cannot be what the others import. What stays under `tests/support/` is the
service's own material -- `environment.py`, its corpus, its fixture blocks --
which is exactly the half a sync must never overwrite.
"""

from __future__ import annotations

from foundry_testkit.aws_kit import AwsKit, AwsKitUnavailable, start_aws_kit
from foundry_testkit.cache_kit import (
    CacheFill,
    CacheUnavailable,
    fill_cache,
    load_cache_fixture,
)
from foundry_testkit.db_seeder import (
    FixtureBlock,
    FixtureFile,
    JsonFixtureSeeder,
    SeedBlock,
    SeedError,
    load_seed_blocks,
    resolve_target_class,
)
from foundry_testkit.resources import ResourceUnavailable, require
from foundry_testkit.settings_surface import environment_variables_read

__all__ = [
    "AwsKit",
    "AwsKitUnavailable",
    "CacheFill",
    "CacheUnavailable",
    "FixtureBlock",
    "FixtureFile",
    "JsonFixtureSeeder",
    "ResourceUnavailable",
    "SeedBlock",
    "SeedError",
    "environment_variables_read",
    "fill_cache",
    "load_cache_fixture",
    "load_seed_blocks",
    "require",
    "resolve_target_class",
    "start_aws_kit",
]
