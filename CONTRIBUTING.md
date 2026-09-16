# Contributing

FIREMaster is one person's retirement model that other people run on their own money. That
shapes what a good pull request looks like here. Read this before opening one.

## One behavior change per PR

A PR changes one thing a user could notice. A tax rule, a new opt-in setting, a bug. If the
description needs a bulleted summary, it is several PRs. Build hygiene and unrelated fixes go
in their own PRs too. Big PRs are not reviewed faster because they are big - they are closed
with a request to split.

## Defaults do not move

Every engine default is neutral or config-driven (`custom_assumptions`), and an existing
install must produce the same numbers before and after your change unless the user opts in.
New behavior ships behind a new key that is off or absent by default. Changing a growth rate,
a withdrawal order, a definition (what "FIRE number" means), or what counts as spendable is a
design discussion first, an issue second, a PR third.

## Snapshot tests are tripwires, not fixtures

`backend/tests/test_projection_snapshots.py` pins engine output for a fixed persona. If your
change moves a pinned number, that is the test doing its job. Do not edit the expected value
in the same commit. Open an issue with the old and new number and why, and wait for a yes. A
PR that rewrites a snapshot without a linked discussion is closed.

## Roles are not tax treatment

Account `fire_role` values describe what an account does in the plan, not how it is taxed.
`retirement_bridge` is the pre-59½ IRA a SEPP draws from - it is not a taxable brokerage.
Money enters the projection pools through `custom_assumptions.sepp`, `taxable_pool`,
`roth_pool`, `rrsp` and `property_sales`, on purpose: the engine cannot tell a SEPP source
from a brokerage from the role alone. Read the "Projection engine" section of CLAUDE.md and
ARCHITECTURE.md before touching `fire_projections.py`, `monte_carlo.py` or `tax_engine.py`.

## Real terms, always

Everything is in today's dollars. Never inflate a flow with `(1+i)**yr`. If you need a nominal
view it is a display concern, not an engine one.

## Keep yourself out of the code

No personal balances, bills, addresses, or account names in fixtures, comments, or defaults.
Use the demo persona (`scripts/seed_demo.py`) or the snapshot persona for examples. State- or
household-specific logic is fine as a selectable model, never as the default.

## Practicalities

- Rebase onto `main` before opening; migrations chain off `alembic heads` on main.
- `cd backend && uv run pytest -v` green, and `cd frontend && npx tsc -b` clean.
- Every new Monarch client method is wrapped with `@_typed_errors` (a 401 is terminal).
- The demo at https://demo.firemaster.io runs on a one-core box. A page that needs seconds of
  CPU per load needs a cache before it can ship.
- Security issues: see SECURITY.md, not a PR.

Small, boring PRs merge in a day. Thank you for reading this far.
