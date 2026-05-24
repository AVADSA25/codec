# Versioning

CODEC follows [Semantic Versioning](https://semver.org/) — `MAJOR.MINOR.PATCH`.

## Source of truth

The current version lives in **one place**: the repo-root [`VERSION`](../VERSION) file,
which mirrors the latest entry in [`CHANGELOG.md`](../CHANGELOG.md). Read it at runtime via:

```python
from codec_version import __version__   # e.g. "2.3.0"
```

`tests/test_versioning.py` pins these together — `VERSION` must equal both
`codec_version.__version__` and the CHANGELOG's newest documented release. Bump all three in
the same commit when cutting a release.

## Cutting a release

1. Add a `## vX.Y.Z (YYYY-MM-DD)` section to `CHANGELOG.md`.
2. Update `VERSION` to `X.Y.Z`.
3. Commit, then tag: `git tag -a vX.Y.Z -m "Release vX.Y.Z (YYYY-MM-DD)" && git push origin vX.Y.Z`.
4. GitHub renders the tag on the Releases page (attach the Mac DMG there for paid builds).

## Retroactively tagging history

The CHANGELOG documents 10 releases (v1.0.0 … v2.3.0) that were never git-tagged.
[`scripts/tag_releases.py`](../scripts/tag_releases.py) creates them, mapping each version to
the last commit on/before its CHANGELOG date. It is **dry-run by default**:

```bash
python3 scripts/tag_releases.py            # preview the version → commit mapping
python3 scripts/tag_releases.py --execute  # create annotated tags locally (review first!)
python3 scripts/tag_releases.py --execute --push   # push them to origin
```

Review the dry-run mapping before executing — the date→commit mapping is best-effort.

## The `v3.0.0` tag — reconciliation needed (operator decision)

The repo carries a `v3.0.0` git tag that is **ahead of the documented history** (the latest
CHANGELOG release is v2.3.0). This is the only real version-drift item left. Pick one:

- **(A, recommended) Treat v3.0.0 as erroneous** — delete it (`git tag -d v3.0.0 && git push
  origin :refs/tags/v3.0.0`) and let `tag_releases.py` lay down v1.0.0…v2.3.0. The next major
  becomes v3.0.0 when it's actually cut.
- **(B) Treat v3.0.0 as the intended next major** — keep the tag, and bump `VERSION` +
  CHANGELOG to `3.0.0` on the next release so the chain reconnects.

This script and `VERSION` deliberately do **not** make that call — deleting/moving a pushed
tag is a published-history change that's the operator's to make.

## Optional follow-up

`codec_dashboard.py` declares the FastAPI app `version="2.1.0"` (stale OpenAPI metadata). It's
a working-runtime string in a hot module, so it's intentionally left untouched here; align it
to `__version__` whenever that module is next edited.
