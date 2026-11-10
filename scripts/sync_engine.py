#!/usr/bin/env python3
"""Vendor the shared Trurl engine from a clean, committed source checkout.

Run from anywhere: python3 scripts/sync_engine.py --source /path/to/trurl --check
Omit --check to refresh. Only the explicit public-file allowlist is read/copied;
personal model state and the standalone installer are never bundled. Managed
references/assets are also projected beneath the Hermes skill for safe loading.
"""

import argparse
import ast
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile


PUBLIC_FILES = (
    "LICENSE",
    "scripts/curation.py",
    "scripts/context.py",
    "scripts/diversity.py",
    "scripts/graph.py",
    "scripts/graph_anchors.py",
    "scripts/graph_updates.py",
    "scripts/sunday.py",
    "scripts/brief.py",
    "scripts/hypotheses.py",
    "scripts/atlas.py",
    "references/domains.json",
    "references/adaptive-weights.md",
    "references/curators-brief.md",
    "references/diverse-discovery.md",
    "references/feedback.md",
    "references/host-compatibility.md",
    "references/knowledge-graph.md",
    "references/onboarding.md",
    "references/state-contract.md",
    "references/sunday-edition.md",
    "references/taste-hypotheses.md",
    "references/visual-identity.md",
    "references/weekly-delivery.md",
    "assets/trurl-wordmark.svg",
    "assets/trurl-wordmark-inverse.svg",
    "assets/taste-atlas-template.html",
)
ENGINE_DIR = Path(__file__).resolve().parents[1] / "engine"
SKILL_PROJECTION_FILES = tuple(name for name in PUBLIC_FILES if name.startswith(("references/", "assets/")))
PLANNER_HEADER = '"""Shared Trurl planning functions. Hermes storage and tools live in workflow.py."""\n'
PLANNER_TRANSFORM = (
    "AST-extracted planning constants/functions through generic_profile; "
    "removed standalone installer constants and unused imports."
)
REFERENCE_NOTE = (
    "> Hermes adapter: use this plugin's native `trurl_*` tools and its storage "
    "bridge. SQLite in the active Hermes profile remains authoritative; the bridge "
    "creates and removes file snapshots internally. Paths and CLI examples below "
    "describe the shared upstream evidence format, not a second persistent model "
    "or this plugin's extracted planner interface. Standalone installation, root "
    "registration and other-host setup apply only to the standalone distribution. "
    "Use Hermes's verified native scheduler for delivery.\n\n"
)
ADAPTATIONS = {
    "scripts/atlas.py": "Rename DATA_TOKEN to DATA_PLACEHOLDER: avoid Hermes hardcoded-secret false positive; placeholder value and behavior unchanged.",
    **{name: "Prefix Hermes native-tool/SQLite bridge note; adapt standalone skill links to the bundled Hermes skill."
       for name in PUBLIC_FILES if name.startswith("references/") and name.endswith(".md")},
}


def _git(source, *args):
    result = subprocess.run(
        ["git", "--no-optional-locks", "-C", str(source), *args],
        text=True, capture_output=True, check=False,
    )
    if result.returncode:
        raise ValueError("Source Git check failed: " + (result.stderr.strip() or " ".join(args)))
    return result.stdout.strip()


def _safe_file(root, relative):
    """Reject symlinked sources/destinations rather than following outside state."""
    path = root / relative
    for part in (path, *path.parents):
        if part == root.parent:
            break
        if part.is_symlink():
            raise ValueError("Symlink not allowed in shared engine path: " + str(part))
    return path


def _planner(source):
    tree = ast.parse(source)
    body = []
    found_generic = False
    found_boundary = False
    removed_imports = set()
    allowed_imports = {"math", "random", "re", "secrets"}
    allowed_from = {"datetime": {"datetime", "timezone"}, "zoneinfo": {"ZoneInfo", "ZoneInfoNotFoundError"}}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "initialize":
            found_boundary = True
            break
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            continue
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            kept = []
            for alias in node.names:
                allowed = (alias.name in allowed_imports if isinstance(node, ast.Import)
                           else node.level == 0 and alias.name in allowed_from.get(node.module, set()))
                if allowed:
                    kept.append(alias)
                else:
                    removed_imports.add(alias.asname or alias.name.split(".")[0])
            if kept:
                node.names = kept
                body.append(node)
            continue
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names = [item.id for target in targets for item in ast.walk(target) if isinstance(item, ast.Name)]
            if any(name.startswith(("INSTALL_", "AGENT_")) for name in names):
                continue
        elif not isinstance(node, ast.FunctionDef):
            raise ValueError("Unexpected top-level planner statement; review the extraction boundary")
        if isinstance(node, ast.FunctionDef) and node.name == "generic_profile":
            found_generic = True
        elif found_generic:
            raise ValueError("Unexpected planner code after generic_profile; review the extraction boundary")
        body.append(node)
    if not found_generic or not found_boundary:
        raise ValueError("Planner must end with generic_profile immediately before initialize")
    extracted = ast.Module(body=body, type_ignores=[])
    used = {node.id for node in ast.walk(extracted) if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)}
    if removed_imports & used:
        raise ValueError("Planner needs an unapproved import: " + ", ".join(sorted(removed_imports & used)))
    return PLANNER_HEADER + ast.unparse(extracted) + "\n"


def _transform(relative, data):
    if relative == "scripts/curation.py":
        return _planner(data.decode("utf-8")).encode("utf-8")
    if relative == "scripts/atlas.py":
        text = data.decode("utf-8")
        if "DATA_TOKEN" not in text:
            raise ValueError("Atlas placeholder changed upstream; review the adapter")
        return text.replace("DATA_TOKEN", "DATA_PLACEHOLDER").encode("utf-8")
    if relative.startswith("references/") and relative.endswith(".md"):
        text = data.decode("utf-8")
        text = text.replace("../SKILL.md#present-the-picks", "../../skills/trurl/SKILL.md#verify-select-and-present-the-works")
        text = text.replace("../SKILL.md#weekly-sunday-delivery", "../../skills/trurl/SKILL.md#sunday-delivery")
        text = text.replace("(../SKILL.md)", "(../../skills/trurl/SKILL.md)")
        return (REFERENCE_NOTE + text).encode("utf-8")
    return data


def skill_projection(outputs):
    """Readable support files inside the skill; leave its custom files alone."""
    projected = {}
    for name in SKILL_PROJECTION_FILES:
        data = outputs[name]
        if name.endswith(".md"):
            data = data.replace(b"../../skills/trurl/SKILL.md", b"../SKILL.md")
        projected[name] = data
    return projected


def expected_engine(source):
    """Build deterministic bytes without executing any upstream Python code."""
    source = Path(source).resolve(strict=True)
    if Path(_git(source, "rev-parse", "--show-toplevel")).resolve() != source:
        raise ValueError("--source must be the root of the Trurl Git checkout")
    commit = _git(source, "rev-parse", "HEAD")
    source_paths = ["skills/trurl/" + name for name in PUBLIC_FILES]
    dirty = _git(source, "status", "--porcelain=v1", "--untracked-files=all", "--", *source_paths)
    if dirty:
        raise ValueError("Shared source files must be committed and clean:\n" + dirty)
    originals = {}
    outputs = {}
    for relative, tracked_path in zip(PUBLIC_FILES, source_paths):
        _git(source, "ls-files", "--error-unmatch", "--", tracked_path)
        path = _safe_file(source, tracked_path)
        if not path.is_file():
            raise ValueError("Missing shared source: " + tracked_path)
        originals[relative] = path.read_bytes()
        # Compare bytes to the pinned commit as well as status, so Git index flags
        # such as assume-unchanged cannot disguise an edited shared source.
        committed = subprocess.run(
            ["git", "--no-optional-locks", "-C", str(source), "show", commit + ":" + tracked_path],
            capture_output=True, check=False,
        )
        if committed.returncode or committed.stdout != originals[relative]:
            raise ValueError("Shared source does not match pinned HEAD: " + tracked_path)
        outputs[relative] = _transform(relative, originals[relative])
    sha = lambda data: hashlib.sha256(data).hexdigest()
    provenance = {
        "format_version": 1,
        "transform_version": 3,
        "upstream_repo": "https://github.com/oblomovunleashed/trurl",
        "upstream_commit": commit,
        "planner_transform": PLANNER_TRANSFORM,
        "files": {name: sha(data) for name, data in originals.items()},
        "adaptations": ADAPTATIONS,
        "output_sha256": {name: sha(data) for name, data in outputs.items()},
        "skill_projection": {
            "directory": "skills/trurl",
            "transform": "Project managed references/assets inside the Hermes skill; make skill links local. Custom Hermes files remain separately owned.",
            "output_sha256": {name: sha(data) for name, data in skill_projection(outputs).items()},
        },
    }
    outputs["SOURCE.json"] = (json.dumps(provenance, indent=2) + "\n").encode("utf-8")
    return outputs


def sync_engine(source, engine_dir=ENGINE_DIR, check=False):
    outputs = expected_engine(source)
    engine_dir = Path(engine_dir).absolute()
    if engine_dir.is_symlink():
        raise ValueError("Engine directory must not be a symlink")
    extra = []
    if engine_dir.exists():
        for path in engine_dir.rglob("*"):
            relative = path.relative_to(engine_dir)
            if "__pycache__" in relative.parts:
                continue
            if path.is_symlink() or (path.is_file() and relative.as_posix() not in outputs):
                extra.append(relative.as_posix())
    if extra:
        raise ValueError("Unexpected engine files; review before syncing: " + ", ".join(sorted(extra)))
    managed = {name: (_safe_file(engine_dir, name), data) for name, data in outputs.items()}
    for name, data in skill_projection(outputs).items():
        relative = "skills/trurl/" + name
        managed[relative] = (_safe_file(engine_dir.parent, relative), data)
    changed = []
    for relative, (path, data) in managed.items():
        if not path.is_file() or path.read_bytes() != data:
            changed.append(relative)
    if check:
        if changed:
            raise ValueError("Engine differs from committed source (including managed skill projections): " + ", ".join(changed))
        return []
    engine_dir.mkdir(parents=True, exist_ok=True)
    # Prepare every output first; individual replacements are atomic.
    with tempfile.TemporaryDirectory(prefix=".trurl-engine-", dir=engine_dir.parent) as directory:
        staging = Path(directory)
        for relative in changed:
            destination = staging / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(managed[relative][1])
        for relative in changed:
            destination = managed[relative][0]
            destination.parent.mkdir(parents=True, exist_ok=True)
            (staging / relative).replace(destination)
    return changed


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="Clean Trurl Git checkout root")
    parser.add_argument("--check", action="store_true", help="Verify bytes and provenance without writing")
    args = parser.parse_args(argv)
    try:
        changed = sync_engine(args.source, check=args.check)
    except (OSError, ValueError, SyntaxError) as error:
        print("Engine sync failed: " + str(error), file=sys.stderr)
        return 1
    print("Shared engine, skill projections and provenance match." if args.check else "Shared engine and skill projections synced (%d files updated)." % len(changed))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
