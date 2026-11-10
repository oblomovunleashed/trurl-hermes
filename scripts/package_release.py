#!/usr/bin/env python3
"""Export a lean, reproducible Hermes install bundle from reviewed source.

This maintainer tool performs local file operations only. It neither publishes
the plugin nor reads Git history, profile state, credentials or agent traces.
The default runtime bundle omits repository documentation, artwork, tests and
maintainer scripts. Use --kind source for the complete reviewed source archive.
"""

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import tempfile
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo


MANIFEST = "release-files.json"
SOURCE_MANIFEST = "source-files.json"
PRIVATE_PARTS = {
    ".git", ".venv", "venv", "__pycache__", "model", "plugin-data",
    "profile", "profiles", "editions", "outputs", "dist", "runtime",
    "trace", "traces", "video-pack", "handoff", "research", "branding",
}
PRIVATE_NAMES = {"taste-atlas.html", "auth.json", "credentials.json", ".ds_store"}
PRIVATE_SUFFIXES = (".pyc", ".pyo", ".jsonl", ".log", ".sqlite", ".sqlite3", ".db", ".zip", ".tar", ".gz")
PUBLIC_DIRECTORIES = (".github", "assets", "engine", "examples", "scripts", "skills", "tests")


def _private(parts):
    lowered = [part.casefold() for part in parts]
    name = lowered[-1]
    return (any(part in PRIVATE_PARTS or part.startswith(".env") for part in lowered)
            or name in PRIVATE_NAMES or name.endswith(PRIVATE_SUFFIXES)
            or any(marker in name for marker in (".sqlite-", ".sqlite3-", ".db-")))


def _path(root, name):
    if not isinstance(name, str) or not name or "\\" in name or ":" in name or "\x00" in name:
        raise ValueError("Invalid release path")
    relative = PurePosixPath(name)
    if (relative.is_absolute() or str(relative) != name or not relative.parts
            or any(part in (".", "..") for part in relative.parts)):
        raise ValueError("Invalid release path: " + name)
    if _private(relative.parts):
        raise ValueError("Private/generated path in release manifest: " + name)
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("Symlink in release path: " + name)
    if not current.is_file():
        raise ValueError("Missing public file: " + name)
    return current


def _manifest_files(root, manifest_name):
    """Validate an explicit allowlist, including its own manifest file."""
    root = Path(root).expanduser().resolve()
    manifest_path = _path(root, manifest_name)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    names = manifest.get("files") if isinstance(manifest, dict) else None
    if (not isinstance(manifest, dict) or manifest.get("format_version") != 1
            or not isinstance(names, list) or not names
            or any(not isinstance(name, str) for name in names)):
        raise ValueError("Release manifest needs format_version 1 and a nonempty list of paths")
    if len(names) != len(set(names)):
        raise ValueError("Duplicate release path")
    if manifest_name not in names:
        raise ValueError("Release manifest must include itself")
    return [(name, _path(root, name)) for name in sorted(names)]


def public_files(root):
    """Return the complete reviewed source boundary, never private state."""
    return _manifest_files(root, SOURCE_MANIFEST)


def release_files(root):
    """Return runtime-only files, all also reviewed in the source boundary."""
    source = {name for name, unused in public_files(root)}
    files = _manifest_files(root, MANIFEST)
    for name, unused in files:
        parts = PurePosixPath(name).parts
        allowed = (parts[0] in ("engine", "skills") if len(parts) > 1 else
                   name in ("LICENSE", "plugin.yaml", MANIFEST) or name.endswith(".py"))
        if not allowed:
            raise ValueError("Repository-only file in runtime manifest: " + name)
        if name not in source:
            raise ValueError("Runtime file missing from source manifest: " + name)
    return files


def check_source(root):
    """Detect omitted package code and bundled resources without consulting Git."""
    root = Path(root).expanduser().resolve()
    declared = {name for name, unused in public_files(root)}
    expected = {path.name for path in root.iterdir()
                if path.is_file() and (path.suffix in (".py", ".md")
                                       or path.name in (".gitignore", "LICENSE", "plugin.yaml", MANIFEST, SOURCE_MANIFEST))}
    for directory in PUBLIC_DIRECTORIES:
        for path in (root / directory).rglob("*"):
            relative = path.relative_to(root)
            if path.is_file() and not _private(relative.parts):
                expected.add(relative.as_posix())
    missing = sorted(expected - declared)
    if missing:
        raise ValueError("Public source files missing from source manifest: " + ", ".join(missing))
    runtime = release_files(root)
    return {"valid": True, "files": len(runtime), "source_files": len(declared),
            "runtime_bytes": sum(path.stat().st_size for unused, path in runtime)}


def package(root, destination, kind="runtime"):
    """Snapshot reviewed bytes; atomically create a fresh tree and archive."""
    if kind not in ("runtime", "source"):
        raise ValueError("Package kind must be runtime or source")
    root = Path(root).expanduser().resolve()
    check_source(root)
    files = release_files(root) if kind == "runtime" else public_files(root)
    snapshot = {}
    for name, source in files:
        before = source.stat()
        data = source.read_bytes()
        after = source.stat()
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
                after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
            raise ValueError("Public source changed during export: " + name)
        snapshot[name] = data
    destination = Path(destination).expanduser()
    if destination.is_symlink():
        raise ValueError("Release output cannot be a symlink")
    destination = destination.resolve()
    if destination == root or root in destination.parents:
        raise ValueError("Release output must be outside the plugin source")
    if destination.exists():
        raise FileExistsError("Release output already exists; choose a fresh --output directory")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".trurl-release-", dir=str(destination.parent)) as temporary:
        staging = Path(temporary) / "release"
        tree = staging / "trurl"
        tree.mkdir(parents=True)
        for name, data in snapshot.items():
            target = tree / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            target.chmod(0o644)
        archive_name = "trurl.zip" if kind == "runtime" else "trurl-source.zip"
        archive_path = staging / archive_name
        with ZipFile(archive_path, "w", compression=ZIP_DEFLATED, compresslevel=9) as archive:
            for name, data in snapshot.items():
                info = ZipInfo("trurl/" + name, date_time=(2026, 1, 1, 0, 0, 0))
                info.compress_type = ZIP_DEFLATED
                info.create_system = 3
                info.external_attr = 0o100644 << 16
                archive.writestr(info, data, compresslevel=9)
        digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
        (staging / "SHA256SUMS").write_text(digest + "  " + archive_name + "\n", encoding="utf-8")
        with ZipFile(archive_path) as archive:
            if archive.namelist() != ["trurl/" + name for name in snapshot]:
                raise ValueError("Release archive contains unexpected paths")
            if any(archive.read("trurl/" + name) != data for name, data in snapshot.items()):
                raise ValueError("Release archive does not match the reviewed source")
        if destination.exists() or destination.is_symlink():
            raise FileExistsError("Release output appeared during export; choose a fresh directory")
        staging.rename(destination)
    return {"directory": str(destination / "trurl"), "archive": str(destination / archive_name),
            "checksums": str(destination / "SHA256SUMS"), "kind": kind,
            "files": len(snapshot), "archive_sha256": digest}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, help="fresh directory outside the plugin source")
    parser.add_argument("--check", action="store_true", help="validate source and runtime manifests without exporting")
    parser.add_argument("--kind", choices=("runtime", "source"), default="runtime",
                        help="runtime install bundle (default) or complete reviewed source")
    args = parser.parse_args(argv)
    if args.check and args.output:
        parser.error("--check and --output are mutually exclusive")
    if not args.check and args.output is None:
        parser.error("export requires a fresh --output directory")
    try:
        result = check_source(args.root) if args.check else package(args.root, args.output, args.kind)
    except (OSError, ValueError) as error:
        parser.exit(1, "Release not created: " + str(error) + "\n")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
