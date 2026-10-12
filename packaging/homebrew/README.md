# Homebrew tap

`mahoraga.rb` is the formula for the `pockanoodles/homebrew-tap` tap. It lives
here so it is versioned with the code; the tap repository holds the copy that
people install from.

```bash
brew install pockanoodles/tap/mahoraga
```

## What the formula installs

The **sdist attached to the GitHub release** (`mahoraga-<version>.tar.gz`), not
the "Source code" archive GitHub generates for a tag. The web UI is built, not
committed, so only the sdist has it inside. The formula's `test do` checks that
the UI is there, so a formula pointed at the wrong archive fails `brew test`.

The Python dependencies are `resource` blocks, built from source into a
virtualenv. The compiled ones that Homebrew already ships for its default
Python (numpy, cryptography, pydantic, rpds-py) come from Homebrew's own
formulae instead, which is why the formula uses `python@3.14` and not 3.12.
Two packages still compile on the user's machine: `jiter` (Rust) and `hnswlib`
(C++, built against Homebrew's numpy and pybind11 with build isolation off).
There are no bottles, so the first install takes a few minutes.

## Create the tap (once)

1. On GitHub, create a **public** repository named `homebrew-tap` under
   `pockanoodles`. The `homebrew-` prefix is what lets `brew` shorten it to
   `pockanoodles/tap`.
2. Tag and release `v2.1.0` first (see `.github/workflows/release.yml`). The
   formula needs the release's sdist to exist, and its checksum.
3. Get the checksum from `SHA256SUMS` on the release, or compute it:

   ```bash
   curl -sL https://github.com/pockanoodles/Mahoraga/releases/download/v2.1.0/mahoraga-2.1.0.tar.gz | shasum -a 256
   ```

4. Copy the formula in and fill the checksum. In `Formula/mahoraga.rb`, replace
   the all-zeros `sha256` and delete the `PLACEHOLDER` comment above it.

   ```bash
   git clone git@github.com:pockanoodles/homebrew-tap.git
   mkdir -p homebrew-tap/Formula
   cp Mahoraga/packaging/homebrew/mahoraga.rb homebrew-tap/Formula/mahoraga.rb
   ```

5. Push it, then check it the way a user would get it:

   ```bash
   cd homebrew-tap && git add Formula/mahoraga.rb && git commit -m "mahoraga 2.1.0" && git push
   brew install --build-from-source pockanoodles/tap/mahoraga
   brew test pockanoodles/tap/mahoraga
   brew audit --strict --online pockanoodles/tap/mahoraga
   ```

`brew test`, `brew audit` and `brew update-python-resources` switch Homebrew's
developer mode on. Turn it off afterwards with `brew developer off`.

## Bump to a new version

1. Release the new version first (a tag runs the release workflow, which also
   publishes to PyPI).
2. In the tap's `Formula/mahoraga.rb`, change the version in `url` and replace
   `sha256` with the new sdist's checksum (as in step 3 above).
3. Regenerate the dependency blocks from what the new version needs:

   ```bash
   brew update-python-resources pockanoodles/tap/mahoraga
   ```

   It reads the version from PyPI, so the new version must already be there. It
   skips the packages listed in `pypi_packages exclude_packages`, which Homebrew
   provides itself. If it reports a failure to build a package, run the same
   command with `--ignore-errors` and read what it could not resolve.
4. Test and push as in step 5 above. Also copy the updated formula back into
   `packaging/homebrew/mahoraga.rb` here so the two stay the same.

## If the PyPI name changes

`mahoraga` on PyPI is a different project. If the package is published under
another name, change `package_name:` in `pypi_packages` to that name, and the
filename in `url` (the sdist is named after the package, with `-` as `_`).
Otherwise `brew update-python-resources` resolves the wrong project.
