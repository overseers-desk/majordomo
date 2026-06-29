# Releasing majordomo

The version lives in `pyproject.toml` and `debian/changelog`; they must match.
The Homebrew formula is **not** in this repo. It lives in the
`overseers-desk/homebrew-ot` tap at `Formula/majordomo.rb` (a separate checkout).

1. **Bump the version** in `pyproject.toml` and add a matching `debian/changelog`
   entry describing the change.

2. **Build the .deb:** `dpkg-buildpackage -us -uc -b` produces
   `../majordomo_<version>_all.deb`.

3. **End-to-end test the .deb before publishing.** Install it from its apt
   dependencies alone (no venv, no pip, no source tree), in a clean container or
   on a test host: `sudo apt install ./majordomo_<version>_all.deb`. Confirm
   `majordomo --help` runs and `majordomo spaces` against a configured cache
   returns rows, and that `majordomo spaces` with no config fails cleanly (not a
   traceback). Do not publish an untested .deb.

4. **Commit** the bump, push `main`, and **cut the GitHub release** with the .deb
   attached: `gh release create v<version> ../majordomo_<version>_all.deb
   --title "majordomo <version>" --notes "…"`.

5. **Bump the Homebrew formula** in the `overseers-desk/homebrew-ot` tap. Once the
   release is published, compute the source-tarball sha256
   (`curl -sL https://github.com/overseers-desk/majordomo/archive/refs/tags/v<version>.tar.gz | sha256sum`)
   and update `url` and `sha256` in `Formula/majordomo.rb`, then commit and push
   in that repo. (If a Python dependency changed, also update its `resource` block.)

6. **Update installed copies.** macOS: `brew update && brew upgrade majordomo`.
   Debian/Ubuntu: install the new `.deb`.
