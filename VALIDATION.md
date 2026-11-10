# Hermes Trurl 0.3.1 validation

The tagged **0.3.1** release was validated on **10 November 2026** against source commit `e6fdae8885770d72247131b76f227520a5645934`, now retained in the private [Trurl archive](https://github.com/oblomovunleashed/trurl-archive/commit/e6fdae8885770d72247131b76f227520a5645934).

The current source checkout re-pins the engine to fresh Trurl `main` commit `3e51e22a98195b5bd57614d19979af4b23b56375` after the upstream history reset. The upstream source tree, generated runtime code and support resources are unchanged; only the recorded upstream commit changes. Both repositories remain private during preparation; the plugin is not yet submitted to the catalog. The tagged release and its archives remain unchanged.

## Current checks

- The initial release passed **110 tests** on Python **3.13.0** in **19.199 seconds**. Packaging regressions extract the actual lean archive, register all ten native tools and generate an Atlas and a dated Sunday edition from the installed resources.
- After the provenance re-pin, all **110 tests** passed again in **20.548 seconds** on Python **3.13.0**.
- Full engine re-sync verification passes against a clean checkout of the fresh upstream commit. Offline bundled verification was also rerun after the re-pin: **27 engine files** and **16 managed skill resources** match their recorded hashes.
- The explicit manifests cover **56 runtime files** and **90 reviewed source files**. The runtime archive omits repository images, screenshots, tests, maintainer scripts and Git history. Source scanning found no iCloud email addresses, personal absolute paths or private profile/demo artifacts in either manifest.
- All **15 local README links** resolve, and all four screenshot checksums match the original captures. Screenshots retain their original capture-commit metadata and use a synthetic profile.
- Official **Hermes 0.23.1**, tag commit `3b421578588243f920886e6fe79a6faa87ea026e`, passes `plugins validate --install-deps --json` for both the source tree and the extracted runtime bundle: **15 checks**, security **safe**, `host_ready=true`, **no warnings**. This uses Python **3.14.9** with the SDK's frozen dependency environment and isolated Hermes profiles.
- Plugin Doctor passes on both layouts with **10 tools**, **0 hooks** and **0 findings** under the same official SDK. No model or scheduler calls are made.

GitHub Actions runs the regression suite, offline integrity checks, manifest checks and runtime/source archive builds on **Python 3.11–3.14**, matching the supported Python range of the validated Hermes SDK. CI does not fetch the private upstream repository; full source re-sync verification is a separate maintainer check.

Catalog admission requires a public, cloneable plugin repository pinned by a full 40-character SHA. The shared engine's reviewable source is vendored in this repository; installation does not fetch the separate upstream repository. Before submitting, make the plugin repository publicly cloneable and run catalog validation against the exact commit in the entry.

## Reproduce

From a source checkout:

```sh
python3 -m unittest discover -s tests -v
python3 scripts/package_release.py --check
python3 scripts/sync_engine.py --check-bundled
python3 scripts/sync_engine.py --source /path/to/clean/trurl --check
hermes plugins doctor . --ci
hermes plugins validate . --install-deps
```

The upstream checkout must be clean and at the recorded commit. Use synthetic state and an isolated Hermes profile. Doctor exercises discovery, loading and registration; `validate --install-deps` additionally checks manifest/capability consistency, compatibility metadata, dependencies and catalog security rules.

Keep raw traces, SQLite databases, personal exports and local runtime harnesses outside the plugin source and catalog submission. Engine synchronization and packaging are maintainer build steps; the installed runtime has no self-updater.

These checks do not establish authenticated model behaviour, live research quality, recommendation enjoyment, scheduler operation or external delivery.
