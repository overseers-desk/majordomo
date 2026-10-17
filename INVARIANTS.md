# Invariants

Rules whose breach is a design change, not a fix; changing one is the owner's decision.

- One importable core holds the entire business behaviour; the front doors (CLI and MCP server) call it and add no logic of their own. The sieve and the credentials live in the core, so no front door can bypass the access gate by accident and a space blocked from AI callers stays blocked through every interface. Logic added to a front door is a path around that gate.
- A library call takes its configuration, credentials and `WORLD_AS_OF` bound as arguments and reads neither the environment nor any file under the home directory itself; `config.toml`, the `.env` beside it and environment variables are read by the front doors and passed in. A core that read them on its own would make every program importing majordomo depend on whatever sits in its host's home, and a cron job with no such setup could not use the library at all.
- Importing the package has no side effect: no file is read, no path is computed from the home directory and no front-door module is loaded at import time. A host program's environment is its own, and an import that touched it would act on the host before the host had called anything.
- The version lives in `pyproject.toml` and `debian/changelog` and the two match: a release is tag-build-publish at the version already in the tree, and a mismatch ships two different claims about what was released.
- The Homebrew formula is not in this repo; it lives in the `overseers-desk/homebrew-od` tap and points at the PyPI sdist. This repo cutting or carrying Homebrew install metadata would give the release two homes that drift.
