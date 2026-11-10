# Hermes Trurl 0.3.1 validation

Release preparation dated **10 November 2026**, with the shared engine pinned to Trurl commit `a63cf71900f85fefeb766c5ec051c1e22d11b697`.

This package was validated locally. The standalone Hermes repository and catalog entry are not published. The results below apply to this release tree, rather than relying on earlier version receipts.

## Current checks

The full plugin suite passes **102 tests** on **Python 3.13.0** in **18.735 seconds**. The engine/source projection check passes against the exact upstream commit above. The public-file manifest contains **81 files**; the release scan found no iCloud email addresses, personal absolute paths or historical runtime-harness references in that payload.

The actual `hermes plugins validate <clean-candidate> --install-deps --json` command passes under official **Hermes 0.23.1**, tag commit `3b421578588243f920886e6fe79a6faa87ea026e`, on **Python 3.14.9** in an SDK environment built from its frozen dependency lock. All **15 checks pass**, the security result is **safe**, `host_ready` is true and the warning list is empty.

The actual Plugin Doctor CLI also passes with **exit 0**, **10 tools**, **0 hooks** and **0 findings** under both current **Hermes 0.23.1** and the declared minimum **Hermes 0.23.0**, on Python **3.14.9**. Source paths and the running Hermes version were verified for each run. The minimum-host check reuses the 0.23.1 frozen dependency environment: it checks loader/API compatibility, not a separate dependency installation for the minimum release.

Catalog admission requires a public, cloneable source repository pinned by a full 40-character SHA. A local validation pass alone does not make this package an admitted catalog entry. After publication, rerun the catalog check against a checkout of the exact public commit used in the entry.

## Reproduce

```sh
python3 -m unittest discover -s tests -v
python3 scripts/sync_engine.py --source /path/to/clean/trurl --check
hermes plugins doctor . --ci
hermes plugins validate . --install-deps
```

Use synthetic state and an isolated Hermes profile. Doctor exercises discovery, loading and registration. `validate --install-deps` additionally checks manifest/capability consistency, compatibility metadata, dependencies and catalog security rules.

Keep raw traces, SQLite databases, personal exports and local runtime harnesses outside the plugin source and catalog submission. Engine synchronization is a maintainer build step, not an installed self-updater.

These automated and native-loader checks do not establish authenticated model behaviour, live research quality, recommendation enjoyment, scheduler operation or external delivery. No live model session or scheduler command was run for these checks.
