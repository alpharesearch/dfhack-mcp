# vendor/dfhack-client-python

The MCP's only non-PyPI dependency, vendored so the repo is self-contained and the
dependency is versioned with it (`pyproject.toml` points the editable path here).

## Identity

| | |
|---|---|
| Upstream | https://github.com/oleksiy-korniychuk/dfhack-client-python |
| Upstream base commit | `de6e584` (Oleksiy, 2025-07-11) |
| Local commits on top | `69aa79e` RPC_REPLY_TEXT carries `fragments`, not `.text`<br>`410aea3` capture RPC_REPLY_TEXT frames + `drain_replies()` |
| Vendored | 2026-11-20, as a file copy (no `.git`) |

## Why a copy and not a submodule/subtree

`git subtree add` failed: the source clone is **shallow** (`shallow roots are not allowed
to be updated`), and `--unshallow` needs network access to GitHub. A submodule would pin a
commit and cannot carry our two local commits. So: a tracked copy with provenance.

## Refreshing from upstream

```bash
# in the sibling clone (needs network to unshallow):
git -C ../dfhack-client-python fetch --unshallow && git -C ../dfhack-client-python pull
rsync -a --exclude=.git --exclude=__pycache__ --exclude='*.egg-info' \
       ../dfhack-client-python/ vendor/dfhack-client-python/
# then re-apply the two local fixes above and run the test suites
```

`../dfhack-client-python` stays the place to pull upstream; `vendor/` is what the MCP
installs. Keep them in sync deliberately - a silent drift between the two is the one real
hazard of vendoring by copy.

`src/dfhack_client_python/py_export/*_pb2.py` are **generated** from `proto/<dfversion>/`
(see the client's `scripts/`); regenerate after adding a new DF proto set.
