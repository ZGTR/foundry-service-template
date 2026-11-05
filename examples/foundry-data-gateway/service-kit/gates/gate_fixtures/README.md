# `scripts/gate_fixtures/` — the both-direction controls for the §9 gates

Every gate here carries **two** fixture packages and runs its PRODUCTION
analyser over both before it issues any verdict on real source:

* `conforming/pkg/` — a correct service, written in deliberately *different*
  wording from the real one. If a gate flags it, **widen the matcher — never
  reword the fixture.** Rewording converts the control into decoration and
  leaves the defect in place.
* `offending/pkg/` — one deliberate violation **per CLAUSE**, each marked
  `GATE_MUST_FLAG_THIS`. One fixture per clause, not one per gate: with two
  offenders in one file a gate can go red for the *wrong* clause, and a red arm
  that agrees with the conclusion you want is indistinguishable from a working
  control.

Each gate asserts a **hand-written literal** count of findings for each arm.
Deriving the expected count from the fixture reduces the assertion to `n == n`
and holds for any detector, including one blinded to the rule it enforces.

## Why the gates must exclude this directory

A fixture good enough to exercise a detector is, by construction, input that
detector's own production scan would find — that is what makes it a control. So
every gate excludes `scripts/gate_fixtures/` from its production population and
**asserts the exclusion in both directions**: it refuses if a fixture path
re-enters the production scan, and it refuses if the fixture tree matches
nothing, because an exclusion credited for an absence it did not cause is not an
exclusion.

The packages here are named `pkg`, not the real package name, for the same
reason: a fixture named after the real package would be resolvable by a real
import and could be picked up by tooling that walks `src/`.
