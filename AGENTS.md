# Project agent memory

- See the README's "Offline tests" section and `.github/workflows/tests.yml` for the supported CI environment and full offline test command. It deselects the exact live Flywheel client test before construction; do not run that test during offline validation.
- `setup.py` declares the installed package dependencies, including the compatibility branch's Flywheel SDK pin. The legacy `setup.cfg` dependency list differs; use the documented package install command.

## Maintaining this file

Keep this file for knowledge useful to almost every future agent session in this project.
Do not repeat what the codebase already shows; point to the authoritative file or command instead.
Prefer rewriting or pruning existing entries over appending new ones.
When updating this file, preserve this bar for all agents and keep entries concise.
