"""§8's `support/`: what this SERVICE brings to its own tests.

The seeding kit, the AWS kit and the cache kit used to live here and now live in
`service-kit/testing/foundry_testkit/`, imported as `foundry_testkit`. They
are byte-identical in every service, so they are template-owned and a sync
replaces them wholesale.

What remains here is the half a sync must never touch: `environment.py`, which
knows THIS service's aggregate, its fixture blocks and its corpus. Nothing in
this package re-exports the kit any more -- a shim would make the ownership
boundary invisible at every call site, which is the one thing the split exists
to make visible. Import the kit by its own name:

    from foundry_testkit import JsonFixtureSeeder, require
    from foundry_testkit.aws_kit import start_aws_kit
"""

from __future__ import annotations

__all__: list[str] = []
