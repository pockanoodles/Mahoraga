# Publishing a release

A release is one tag. The one-time setup below is done by hand in the PyPI and
GitHub web pages; after that, `git push origin vX.Y.Z` runs
`.github/workflows/release.yml`, which builds the UI and the packages,
smoke-tests the wheel in a clean virtualenv, and publishes PyPI, a GitHub
Release and a multi-arch image on `ghcr.io`. The Homebrew tap is updated by hand
afterwards ([homebrew/README.md](homebrew/README.md)).

## What each channel installs

| Channel | Command | Gets |
| --- | --- | --- |
| PyPI (uv, pipx, pip) | `uv tool install <pypi-name>` | the wheel: engine, CLI, the built app, the demo |
| Homebrew | `brew install pockanoodles/tap/mahoraga` | the release sdist, same contents as the wheel |
| Docker | `docker run --rm -p 127.0.0.1:8000:8000 ghcr.io/pockanoodles/mahoraga` | the engine and the app; Ollama is reached on the host |
| Git | `uv tool install git+https://github.com/pockanoodles/Mahoraga` | the engine and CLI built from source, **without the app** |

The app is built from `frontend/`, not committed, so only the wheel, sdist and
image carry it. A git install serves the API and says "no UI built in" at `/`;
`mahoraga serve --headless` and every `mahoraga audit` command work the same.
Pin a release with `...Mahoraga@v2.1.0`.

## Before the first tag

1. **Pick the PyPI name.** It is `name` in `pyproject.toml`. `mahoraga` on PyPI
   is an unrelated project (a reverse proxy for Python mirrors), so it cannot be
   used; the README and the site say `mahoraga-audit`. The command users type
   stays `mahoraga` either way. A rename touches `pyproject.toml`, the sdist
   file name and `pypi_packages package_name` in `homebrew/mahoraga.rb`, the
   `environment.url` in `release.yml`, and anything that looks the package up by
   name with `importlib.metadata`.
2. **Try the pipeline without publishing.** Actions, then Release, then Run
   workflow, on `main`. It runs the build, the smoke test and the real multi-arch
   Docker build, and publishes nothing.
3. **PyPI trusted publisher.** At pypi.org/manage/account/publishing add a
   *pending publisher* (GitHub): the project name from step 1, owner
   `pockanoodles`, repository `Mahoraga`, workflow `release.yml`, environment
   `pypi`. No token is stored anywhere. The name is not reserved until the first
   upload, so tag soon after.
4. **GitHub settings.** Environments, then New environment `pypi` (add yourself
   as a required reviewer to get an approval prompt before each upload). Code
   security, then enable private vulnerability reporting, which `SECURITY.md`
   points to.

## Cut the release

1. `backend/orchestrator/__version__.py` holds the version on `main`.
2. `git tag v2.1.0 && git push origin v2.1.0`. The workflow refuses a tag that
   differs from that file. PyPI never accepts a version twice, so a bad release
   is yanked and fixed in the next one (`2.1.1`).
3. Watch the run. If PyPI is waiting on the `pypi` environment, approve it. A
   failed job can be re-run on its own from the Actions page.

## After the run

1. **GHCR visibility.** A new package starts private. Profile, Packages,
   `mahoraga`, Package settings, change visibility to public. Check with
   `docker pull ghcr.io/pockanoodles/mahoraga` from a shell that is not logged in.
2. **Homebrew.** Create `pockanoodles/homebrew-tap` and add the formula with the
   sdist checksum from the release's `SHA256SUMS`
   ([homebrew/README.md](homebrew/README.md)).
3. **The site.** Flip `live` to `true` for each channel in
   `site/content/channels.ts`, then deploy.
4. **Try each channel from a clean shell**, the way a user would:

   ```bash
   uv tool install <pypi-name>      && mahoraga version
   pipx install <pypi-name>         && mahoraga version
   brew install pockanoodles/tap/mahoraga && mahoraga version
   docker run --rm ghcr.io/pockanoodles/mahoraga version
   ```

## Checking a build by hand

```bash
bash scripts/build_ui.sh                  # the app into backend/orchestrator/service/ui
python -m build                           # sdist and wheel in dist/
python scripts/smoke_wheel.py dist/*.whl  # clean venv: commands, app, demo, server
```

`scripts/build_ui.sh` has to run first, or the wheel has no app in it.
