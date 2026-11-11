# Hermes Trurl 0.3.1 validation

The tagged **0.3.1** release was validated on **10 November 2026** against its original vendored source snapshot. Its tag and release archives remain unchanged.

The current source checkout pins the engine to Trurl commit `3e51e22a98195b5bd57614d19979af4b23b56375`. Later changes add research documentation and refresh source provenance; generated engine runtime code and support resources are unchanged. All reviewable engine source and licensing are included in this repository. The standalone upstream repository is not an installation dependency.

## Current checks

Final publication checks were rerun on **11 November 2026** using isolated synthetic profiles.

- The initial release passed **110 tests** on Python **3.13.0** in **19.199 seconds**. Packaging regressions extract the actual lean archive, register all ten native tools and generate an Atlas and a dated Sunday edition from the installed resources.
- The current candidate passes all **110 tests** in **19.690 seconds** on Python **3.13.0**.
- Full engine re-sync verification passes against a clean checkout of the fresh upstream commit. Offline bundled verification was also rerun after the re-pin: **27 engine files** and **16 managed skill resources** match their recorded hashes.
- The explicit manifests cover **56 runtime files** and **92 reviewed source files**. The runtime archive omits repository images, screenshots, tests, maintainer scripts and Git history. Source scanning found no iCloud email addresses, personal absolute paths or private profile/demo artifacts in either manifest.
- All **18 local README links** resolve, and all four screenshot checksums match the original captures. Screenshots retain their original capture-commit metadata and use a synthetic profile.
- The final publication candidate was also checked with official **Hermes 0.23.1**, tag commit `3b421578588243f920886e6fe79a6faa87ea026e`, passes `plugins validate --install-deps --json` for both the source tree and the extracted runtime bundle: **15 checks**, security **safe**, `host_ready=true`, **no warnings**. This uses Python **3.14.9** with the SDK's frozen dependency environment and isolated Hermes profiles.
- Plugin Doctor passes on both layouts with **10 tools**, **0 hooks** and **0 findings** under the same official SDK. No model or scheduler calls are made.

GitHub Actions runs the regression suite, offline integrity checks, manifest checks and runtime/source archive builds on **Python 3.11–3.14**, matching the supported Python range of the validated Hermes SDK. CI does not fetch the private upstream repository; full source re-sync verification is a separate maintainer check.

Catalog admission requires a public, cloneable plugin repository pinned by a full 40-character SHA. The shared engine's reviewable source is vendored in this repository; installation does not fetch the separate upstream repository. Catalog CI validates the exact commit in the entry before maintainer review.

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
