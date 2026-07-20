# Invariants

Rules whose breach is a design change, not a fix; changing one is the owner's decision.

- One importable core holds the entire business behaviour, and the front doors (the CLI and the MCP server) call it adding no logic of their own: the sieve and the credentials live in the core, so no front door can bypass the access gate by accident, and a space blocked from AI callers stays blocked through every interface. Logic added to a front door is a path around that gate.
- The version lives in `pyproject.toml` and `debian/changelog` and the two match: a release is tag-build-publish at the version already in the tree, and a mismatch ships two different claims about what was released.
- The Homebrew formula is not in this repo; it lives in the `overseers-desk/homebrew-od` tap and points at the PyPI sdist: this repo cutting or carrying install metadata for Homebrew would give the release two homes that drift.
