# Releasing majordomo

The version lives in `pyproject.toml` and `debian/changelog`; they must match.
"Release X.Y.Z" publishes the version already in the tree, not a new number.

PyPI (`pip install majordomo`) is the primary install channel. The Homebrew
formula is **not** in this repo; it lives in the `overseers-desk/homebrew-od`
tap at `Formula/majordomo.rb`, and its `url`/`sha256` point at the PyPI sdist
(the same tarball pip downloads), not the GitHub archive.

1. **Sync the version** in `pyproject.toml` and `debian/changelog`, with the
   changelog describing the user-visible changes.

2. **Commit** the changes and push `main`.

3. **Tag and push:** `git tag vX.Y.Z && git push origin main vX.Y.Z`.

4. **Publish to PyPI** — the primary channel. Build the distribution and upload
   both the sdist and the wheel:

   ```bash
   uv build   # produces dist/majordomo-X.Y.Z.tar.gz (sdist) and the wheel
   uvx twine upload --non-interactive \
     dist/majordomo-X.Y.Z.tar.gz dist/majordomo-X.Y.Z-py3-none-any.whl
   ```

   Credentials come from `~/.pypirc`; do not read or print that file. `uv publish`
   does not read `~/.pypirc`, which is why twine is used here. Verify the version
   is live: `curl -sL https://pypi.org/pypi/majordomo/json` should show
   `info.version == X.Y.Z`.

5. **Build the .deb:** `dpkg-buildpackage -us -uc -b` produces
   `../majordomo_X.Y.Z_all.deb`.

6. **End-to-end test the .deb before publishing.** Install it from its apt
   dependencies alone (no venv, no pip, no source tree), in a clean container or
   on a test host: `sudo apt install ./majordomo_X.Y.Z_all.deb`. Confirm
   `majordomo --help` runs and `majordomo spaces` against a configured cache
   returns rows, and that `majordomo spaces` with no config fails cleanly (not a
   traceback). Do not publish an untested .deb.

7. **Cut the GitHub release** with the .deb attached:
   `gh release create vX.Y.Z ../majordomo_X.Y.Z_all.deb --title "majordomo X.Y.Z" --notes "…"`.

8. **Bump the Homebrew formula** in the `overseers-desk/homebrew-od` tap. After
   the PyPI publish, read the sdist `url` and `sha256` from the PyPI JSON — the
   object in `.urls` where `packagetype == "sdist"`, its `.url` and
   `.digests.sha256` — and update them in `Formula/majordomo.rb`, then commit and
   push in that repo. (If a Python dependency changed, also update its `resource`
   block.)

   ```bash
   curl -sL https://pypi.org/pypi/majordomo/X.Y.Z/json
   ```

9. **Update installed copies.** macOS: `brew update && brew upgrade majordomo`.
   Debian/Ubuntu: install the new `.deb`. PyPI/uv: `uv tool upgrade majordomo`.
