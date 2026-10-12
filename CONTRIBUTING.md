# Contributing

Thanks for looking. Bug reports, fixes and ideas are all welcome. For anything
bigger than a fix, open an issue first so we can agree on the shape before you
spend the time.

## Set up

You need Python 3.12 or newer, and a C++ compiler (`hnswlib` builds from source:
`xcode-select --install` on macOS, `build-essential` on Debian and Ubuntu).

```bash
git clone https://github.com/pockanoodles/Mahoraga.git
cd Mahoraga
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
```

That gives you `mahoraga` (and `orch`, the same program). Ollama and the coding
agents (aider, opencode) are only needed to run a real audit, not to work on the
code or run the tests.

The web app lives in `frontend/`. Run `mahoraga serve` in one terminal and
`npm ci && npm run dev` in `frontend/` in another; the dev server on
`localhost:5173` proxies `/api` to the engine on port 8000.

## Test

```bash
pytest -m "not slow"
```

This is what CI runs, and it takes a minute or two. The tests mock Ollama and
every HTTP call, so they need no models, no network and no API key. The `slow`
marker is for tests that load the real embedding model; leave those out unless
you changed that code.

## Branches and pull requests

`main` is the trunk and is always releasable. Work on a short-lived branch named
for the kind of change (`feat/`, `fix/`, `chore/`, `docs/`), open a pull request,
and merge once CI is green. Commits are one plain sentence with a type prefix,
the way `git log` already reads: `fix: mine tests the task's code in src-layout
repos`.

Match the code around you. A module's docstring says why it exists, comments are
short, modules stay small and single-purpose, and dead code is deleted rather than
commented out (git keeps it).

## The claims rule

A number is publishable only through `experiments/claims.json` and `orch bench
verify`. Every figure quoted in the README, the docs or the website must be a
claim in that file, bound to the committed per-case artifact it was computed from.
`orch bench verify` recomputes each one and fails the build if the digits differ,
so a published number cannot drift from its data. To publish a new number, commit
the artifact and add the claim. To change one, change the data behind it.

## Releases

A release is a tag. Set the version in `backend/orchestrator/__version__.py`,
merge, then push `vX.Y.Z`. The Release workflow builds the UI and the packages,
checks the tag matches the version, and publishes to PyPI, GitHub Releases and
`ghcr.io`. To try the build without publishing, run the workflow by hand from a
branch. `scripts/build_ui.sh` puts the built UI inside the package if you want to
build a wheel yourself.

## Security

Please report vulnerabilities privately; see [SECURITY.md](SECURITY.md).
