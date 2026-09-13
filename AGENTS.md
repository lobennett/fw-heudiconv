# Project agent memory

- This compatibility fork is consumed from `sherlock-compat`. Keep that branch
  as the publication/review base; no-mistakes runs use `--base-branch sherlock-compat`.
- See [docs/CODE-REVIEW.md](docs/CODE-REVIEW.md) for selection/export invariants,
  synthetic regression commands and audit limits.
- Run offline tests with `python -m pytest testing -k 'not test_client'`.
  `test_client` requires credentials; the legacy CircleCI workflow also performs
  live Flywheel mutations. Do not use it as an offline verification command.

## Maintaining this file

Keep this file for knowledge useful to almost every future agent session in this project.
Do not repeat what the codebase already shows; point to the authoritative file or command instead.
Prefer rewriting or pruning existing entries over appending new ones.
When updating this file, preserve this bar for all agents and keep entries concise.
