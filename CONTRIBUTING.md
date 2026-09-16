# Contributing

The short version: **bug reports are welcome; code contributions are not being sought.**

FIREMaster is a personal tool, published so that the people running it can audit what
touches their financial data. It is built and maintained by one person, for his own
retirement first. It is not a community project, and this file is here so nobody has to
discover that through an awkward PR review.

## Bug reports — yes

If something is broken, an issue with reproduction steps is genuinely appreciated:

- What you did, what you expected, what happened instead
- Container or native path, OS, and the relevant log lines
  (`docker compose logs backend` usually has the story)
- For projection/engine questions: the config values involved (never post real account
  data — see below)

There is **no SLA**. Issues get read; fixes happen when they happen.

## Pull requests — closed by default

Please open an issue *before* writing any code. Unsolicited PRs will generally be closed
unread, regardless of quality — not out of disrespect, but because:

- Every merged line becomes a maintenance obligation on one person
- The engine encodes deliberate financial-modeling decisions that look like bugs until
  they aren't (see `ARCHITECTURE.md`)
- Keeping the copyright uniform preserves the project's licensing freedom

If an issue discussion ends with "a PR for this would be accepted," that's the invitation.
Anything merged requires agreement that the contribution is licensed to the project's
copyright holder: the PR template carries the sentence, and a CI check stays red until the
PR description contains it. No sentence, no merge — including cherry-picks.

## Feature requests

You can file them, with expectations set accordingly: the roadmap is whatever the author
needs next. The good news is that the API-first design means many "features" don't need
code — point Claude Code (or any agent) at the backend API and ask your question. That's
the intended extension mechanism.

## If a PR was invited

The engine encodes deliberate decisions, and other people run it on their own money. So an
invited PR follows these, or it comes back:

- **One behavior change per PR.** If the description needs a bulleted summary, it is several
  PRs. Build hygiene and unrelated fixes go separately.
- **Defaults do not move.** An existing install produces the same numbers before and after
  your change unless the user opts in. New behavior ships behind a new `custom_assumptions`
  key that is absent by default. Changing a rate, a withdrawal order, a definition, or what
  counts as spendable is an issue first.
- **Snapshot tests are tripwires, not fixtures.** If `test_projection_snapshots.py` moves,
  that is the test working. Do not edit the expected value in the same PR — open an issue
  with old and new number and why.
- **Roles are not tax treatment.** `fire_role` says what an account does in the plan.
  `retirement_bridge` is the pre-59½ IRA a SEPP draws from, not a taxable brokerage. Money
  enters the projection pools through `custom_assumptions.sepp` / `taxable_pool` /
  `roth_pool` / `rrsp` / `property_sales` on purpose. Read CLAUDE.md's "Projection engine"
  section and ARCHITECTURE.md before touching `fire_projections.py`, `monte_carlo.py`, or
  `tax_engine.py`.
- **Real terms, always.** Never inflate a flow with `(1+i)**yr`.
- **Keep yourself out of the code.** No personal balances, bills, or account names in
  fixtures, comments, or defaults; no state- or household-specific logic as the default.
- Rebase onto `main`; `cd backend && uv run pytest -v` green; `cd frontend && npx tsc -b`
  clean; every new Monarch client method wrapped with `@_typed_errors`. The demo runs on a
  one-core box — seconds of CPU per page load needs a cache first.

## One hard rule

**Never post real financial data** — account numbers, balances, institution names tied to
amounts, transaction exports — in issues, PRs, or discussions. Redact first. Reports
containing PII may be deleted outright to keep it out of search indexes.

## Security issues

Not here — see [SECURITY.md](SECURITY.md) for the private disclosure route.
