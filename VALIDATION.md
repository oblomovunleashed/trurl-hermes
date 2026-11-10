# Hermes Trurl 0.3.1 validation

Release preparation dated **10 November 2026**, with the shared engine pinned to Trurl commit `a63cf71900f85fefeb766c5ec051c1e22d11b697`.

The runtime was validated locally at initial release commit `125981544d5463bc8f386181770511573d7ab1b1`. The standalone Hermes repository is private and the catalog entry is not published. The native validation results below describe that initial release; subsequent README and screenshot additions do not change runtime code.

## Current checks

At initial release, the full plugin suite passed **102 tests** on **Python 3.13.0** in **18.735 seconds**. The engine/source projection check passed against the exact upstream commit above. The initial public-file manifest contained **81 files**; the release scan found no iCloud email addresses, personal absolute paths or historical runtime-harness references in that payload.

After adding the screenshots, all **102 tests** passed again in **17.486 seconds**. The current **88-file** manifest passes the release check, all local README links resolve, and the four image checksums match their original captures.

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
