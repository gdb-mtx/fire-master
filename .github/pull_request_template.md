<!-- Read CONTRIBUTING.md first: issue before code, one behavior change per PR, defaults do not move. -->

## What changes, in one sentence

## Which issue invited it

Closes #

## Checks

- [ ] One behavior change. Build hygiene and unrelated fixes are separate PRs.
- [ ] An existing install produces the same numbers before and after, or the change is behind a new opt-in key.
- [ ] No snapshot expectation edited. If one moved, the linked issue has old, new, and why.
- [ ] No personal balances, bills, account names, or state-specific defaults.
- [ ] `cd backend && uv run pytest -v` green, `cd frontend && npx tsc -b` clean.

## License

The line below must stay in the description exactly as written or the license check fails and the PR cannot be merged.

I license this contribution to the FIREMaster copyright holder under the terms in CONTRIBUTING.md.
