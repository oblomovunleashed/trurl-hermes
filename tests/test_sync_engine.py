"""Test provenance, drift detection and the public-file boundary with a toy repo."""

import hashlib
import importlib.util
import json
from pathlib import Path
import re
import runpy
import shutil
import subprocess
from tempfile import TemporaryDirectory
import unittest
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("trurl_sync_engine", ROOT / "scripts" / "sync_engine.py")
sync = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sync)

PLANNER = '''"""Upstream planner and installer."""
import math
import os
import random
from datetime import datetime, timezone
INSTALL_EXCLUSIONS = ("model",)
AGENT_COMMANDS = {"codex": "codex"}
AXES = ("depth", "novelty")
def make_plan(value):
    return math.sqrt(value)
def generic_profile():
    return {"interests": [], "created_at": datetime.now(timezone.utc).isoformat()}
def initialize(root):
    os.mkdir(root)
'''


class SyncEngineTest(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.source = self.base / "source"
        self.source.mkdir()
        self.engine = self.base / "plugin" / "engine"
        for name in sync.PUBLIC_FILES:
            path = self.source / "skills" / "trurl" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("Public fixture: " + name + "\n")
        self.shared("scripts/curation.py").write_text(PLANNER)
        self.shared("scripts/atlas.py").write_text('DATA_TOKEN = "__TRURL_ATLAS_DATA__"\nprint(DATA_TOKEN)\n')
        self.shared("references/curators-brief.md").write_text("# Brief\n[Present](../SKILL.md#present-the-picks)\n")
        self.shared("references/onboarding.md").write_text("# Onboarding\n[Delivery](../SKILL.md#weekly-sunday-delivery)\n")
        self.shared("references/sunday-edition.md").write_text("# Edition\n[Skill](../SKILL.md)\n")
        self.git("init", "-q")
        self.git("add", "skills/trurl")
        self.commit()

    def git(self, *args):
        return subprocess.check_output([
            "git", "-C", str(self.source), "-c", "user.name=Trurl Test",
            "-c", "user.email=trurl-test@example.invalid", "-c", "core.hooksPath=/dev/null",
            "-c", "commit.gpgsign=false", *args,
        ], text=True).strip()

    def commit(self):
        self.git("commit", "-qm", "Synthetic shared sources")

    def shared(self, name):
        return self.source / "skills" / "trurl" / name

    def test_exact_pin_hashes_pure_planner_and_adapters(self):
        changed = sync.sync_engine(self.source, self.engine)
        self.assertEqual(set(changed), set(sync.PUBLIC_FILES) | {"SOURCE.json"} |
                         {"skills/trurl/" + name for name in sync.SKILL_PROJECTION_FILES})
        provenance = json.loads((self.engine / "SOURCE.json").read_text())
        self.assertEqual(provenance["upstream_commit"], self.git("rev-parse", "HEAD"))
        for name in sync.PUBLIC_FILES:
            self.assertEqual(provenance["files"][name], hashlib.sha256(self.shared(name).read_bytes()).hexdigest())
            self.assertEqual(provenance["output_sha256"][name], hashlib.sha256((self.engine / name).read_bytes()).hexdigest())
        planner = (self.engine / "scripts/curation.py").read_text()
        self.assertNotIn("INSTALL_", planner)
        self.assertNotIn("AGENT_", planner)
        self.assertNotIn("initialize", planner)
        self.assertNotIn("import os", planner)
        namespace = {}
        exec(compile(planner, "<synthetic extracted planner>", "exec"), namespace)
        self.assertEqual(namespace["make_plan"](81), 9)
        self.assertEqual(namespace["generic_profile"]()["interests"], [])
        self.assertIn("DATA_PLACEHOLDER", (self.engine / "scripts/atlas.py").read_text())
        self.assertNotIn("DATA_TOKEN", (self.engine / "scripts/atlas.py").read_text())
        reference = (self.engine / "references/curators-brief.md").read_text()
        self.assertTrue(reference.startswith(sync.REFERENCE_NOTE))
        self.assertIn("../../skills/trurl/SKILL.md#verify-select-and-present-the-works", reference)
        self.assertEqual(provenance["transform_version"], 4)
        for name in sync.PUBLIC_FILES:
            if name.startswith("references/") and name.endswith(".md"):
                self.assertTrue((self.engine / name).read_text().startswith(sync.REFERENCE_NOTE), name)
                self.assertIn(name, provenance["adaptations"])
        self.assertIn("../../skills/trurl/SKILL.md#sunday-delivery", (self.engine / "references/onboarding.md").read_text())
        self.assertIn("(../../skills/trurl/SKILL.md)", (self.engine / "references/sunday-edition.md").read_text())
        projected = self.engine.parent / "skills/trurl"
        self.assertEqual(set(provenance["skill_projection"]["output_sha256"]), set(sync.SKILL_PROJECTION_FILES))
        for name in sync.SKILL_PROJECTION_FILES:
            data = (projected / name).read_bytes()
            self.assertEqual(provenance["skill_projection"]["output_sha256"][name], hashlib.sha256(data).hexdigest())
        self.assertIn("../SKILL.md#verify-select-and-present-the-works", (projected / "references/curators-brief.md").read_text())
        self.assertIn("../SKILL.md#sunday-delivery", (projected / "references/onboarding.md").read_text())
        self.assertIn("(../SKILL.md)", (projected / "references/sunday-edition.md").read_text())
        self.assertNotIn("../../skills/", (projected / "references/curators-brief.md").read_text())

    def test_allowlist_includes_new_workflow_dependencies_without_standalone_storage(self):
        required = {"LICENSE", "scripts/context.py", "scripts/graph_anchors.py", "scripts/graph_updates.py",
                    "references/knowledge-graph.md", "references/state-contract.md", "references/feedback.md"}
        self.assertTrue(required.issubset(sync.PUBLIC_FILES))
        self.assertNotIn("SKILL.md", sync.PUBLIC_FILES)
        self.assertNotIn("scripts/location.py", sync.PUBLIC_FILES)
        self.assertEqual(len(sync.PUBLIC_FILES), len(set(sync.PUBLIC_FILES)))

    def test_standalone_commands_become_native_calls_in_both_reference_copies(self):
        self.shared("references/knowledge-graph.md").write_text(
            '# Evidence rules\n\nUse `python3 <skill-directory>/scripts/context.py prepare --root <curation-root>`.\n\n'
            '```sh\npython3 scripts/curation.py graph-update --root /private/model \\\n'
            '  --delta /private/delta.json --batch-id sample\n'
            'python3 scripts/curation.py graph-update-check --root /private/model --batch-id sample\n```\n\n'
            '```json\n{"support_note": "A source establishes a fact, never enjoyment.", "source_provenance": ["s1"]}\n```\n\n'
            'Keep original anchors separate from content relationships.\n')
        self.git("add", "skills/trurl/references/knowledge-graph.md")
        self.commit()
        sync.sync_engine(self.source, self.engine)
        for base in (self.engine, self.engine.parent / "skills/trurl"):
            text = (base / "references/knowledge-graph.md").read_text()
            self.assertIn('trurl_context(action="prepare")', text)
            self.assertIn('trurl_graph(action="complete", plan_id=..., candidates=..., review=...', text)
            self.assertIn('trurl_graph(action="check", plan_id=..., candidates=...)', text)
            self.assertIn('"support_note": "A source establishes a fact, never enjoyment."', text)
            self.assertIn("Keep original anchors separate", text)
            self.assertNotRegex(text, r"python3|scripts/|--root|--delta|```sh")
        self.assertTrue(sync.check_bundled(self.engine)["valid"])

    def test_unrecognized_standalone_commands_stop_sync_before_writing(self):
        for command in ('python3 scripts/curation.py new-dangerous-action --root /private/model',
                        'python3 scripts/future.py run', 'echo do-not-run-this'):
            with self.subTest(command=command):
                with self.assertRaisesRegex(ValueError, "Unrecognized standalone command"):
                    sync._transform("references/knowledge-graph.md", ('```sh\n' + command + '\n```\n').encode())
        self.assertFalse(self.engine.exists())

    def test_bundled_check_needs_no_upstream_and_detects_content_or_provenance_drift(self):
        sync.sync_engine(self.source, self.engine)
        shutil.rmtree(self.source)
        before = {str(path): path.read_bytes() for path in self.engine.parent.rglob("*") if path.is_file()}
        result = sync.check_bundled(self.engine)
        self.assertTrue(result["valid"])
        self.assertEqual(result["engine_files"], len(sync.PUBLIC_FILES))
        self.assertEqual(before, {str(path): path.read_bytes() for path in self.engine.parent.rglob("*") if path.is_file()})
        cases = [self.engine / "scripts/diversity.py",
                 self.engine.parent / "skills/trurl/references/knowledge-graph.md"]
        for path in cases:
            with self.subTest(path=path):
                original = path.read_bytes()
                path.write_bytes(original + b"Drift\n")
                with self.assertRaisesRegex(ValueError, "differs"):
                    sync.check_bundled(self.engine)
                path.write_bytes(original)
        manifest = self.engine / "SOURCE.json"
        original = manifest.read_bytes()
        data = json.loads(original)
        data["transform_version"] = 3
        manifest.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, "adapter version"):
            sync.check_bundled(self.engine)
        manifest.write_bytes(original)
        extra = self.engine / "scripts/unreviewed.py"
        extra.write_text("raise RuntimeError('Must never execute during checking')\n")
        with self.assertRaisesRegex(ValueError, "file set differs"):
            sync.check_bundled(self.engine)

    def test_check_and_repeat_sync_are_readonly_when_current(self):
        sync.sync_engine(self.source, self.engine)
        before = {str(path): path.stat().st_mtime_ns for path in self.engine.parent.rglob("*") if path.is_file()}
        self.assertEqual(sync.sync_engine(self.source, self.engine, check=True), [])
        self.assertEqual(sync.sync_engine(self.source, self.engine), [])
        self.assertEqual(before, {str(path): path.stat().st_mtime_ns for path in self.engine.parent.rglob("*") if path.is_file()})

    def test_projection_drift_is_checked_and_repaired_without_changing_custom_skill_files(self):
        sync.sync_engine(self.source, self.engine)
        skill = self.engine.parent / "skills/trurl"
        custom = {"SKILL.md": "Custom native Hermes skill\n", **{
            "references/hermes-" + name + ".md": "Custom Hermes " + name + "\n"
            for name in ("onboarding", "feedback", "weekly-delivery")}}
        for name, text in custom.items():
            (skill / name).write_text(text)
        projected = skill / "references/knowledge-graph.md"
        original = projected.read_bytes()
        projected.write_text("Projection drift\n")
        before = {str(path): path.read_bytes() for path in self.engine.parent.rglob("*") if path.is_file()}
        with self.assertRaisesRegex(ValueError, "skills/trurl/references/knowledge-graph.md"):
            sync.sync_engine(self.source, self.engine, check=True)
        self.assertEqual(before, {str(path): path.read_bytes() for path in self.engine.parent.rglob("*") if path.is_file()})
        self.assertEqual(sync.sync_engine(self.source, self.engine), ["skills/trurl/references/knowledge-graph.md"])
        self.assertEqual(projected.read_bytes(), original)
        for name, text in custom.items():
            self.assertEqual((skill / name).read_text(), text)
        self.assertEqual(sync.sync_engine(self.source, self.engine, check=True), [])

    def test_projection_symlink_fails_before_any_output_is_updated(self):
        sync.sync_engine(self.source, self.engine)
        reference = self.engine.parent / "skills/trurl/references/curators-brief.md"
        external = self.base / "do-not-write.txt"
        external.write_text("Preserved external contents")
        reference.unlink()
        reference.symlink_to(external)
        engine_before = {str(path): path.read_bytes() for path in self.engine.rglob("*") if path.is_file()}
        with self.assertRaisesRegex(ValueError, "Symlink not allowed"):
            sync.sync_engine(self.source, self.engine)
        self.assertEqual(external.read_text(), "Preserved external contents")
        self.assertEqual(engine_before, {str(path): path.read_bytes() for path in self.engine.rglob("*") if path.is_file()})

    def test_check_catches_output_and_provenance_drift_without_repair(self):
        sync.sync_engine(self.source, self.engine)
        changed = self.engine / "scripts/diversity.py"
        changed.write_text("accidental edit\n")
        with self.assertRaisesRegex(ValueError, "scripts/diversity.py"):
            sync.sync_engine(self.source, self.engine, check=True)
        self.assertEqual(changed.read_text(), "accidental edit\n")
        sync.sync_engine(self.source, self.engine)
        manifest = self.engine / "SOURCE.json"
        data = json.loads(manifest.read_text())
        data["upstream_commit"] = "0" * 40
        manifest.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, "SOURCE.json"):
            sync.sync_engine(self.source, self.engine, check=True)

    def test_shared_dirty_or_index_hidden_edits_fail_before_writing(self):
        sync.sync_engine(self.source, self.engine)
        manifest = (self.engine / "SOURCE.json").read_bytes()
        name = "scripts/diversity.py"
        self.shared(name).write_text("uncommitted shared change\n")
        with self.assertRaisesRegex(ValueError, "committed and clean"):
            sync.sync_engine(self.source, self.engine)
        self.git("update-index", "--assume-unchanged", "skills/trurl/" + name)
        with self.assertRaisesRegex(ValueError, "does not match pinned HEAD"):
            sync.sync_engine(self.source, self.engine)
        self.assertEqual((self.engine / "SOURCE.json").read_bytes(), manifest)

    def test_private_and_unrelated_source_files_are_never_copied(self):
        (self.source / "model").mkdir()
        (self.source / "model/profile.json").write_text('{"private":"PRIVATE_FIXTURE"}')
        (self.source / "notes.txt").write_text("Uncommitted private notes")
        self.shared("model/profile.json").parent.mkdir()
        self.shared("model/profile.json").write_text("PRIVATE_FIXTURE")
        sync.sync_engine(self.source, self.engine)
        files = {path.relative_to(self.engine).as_posix() for path in self.engine.rglob("*") if path.is_file()}
        self.assertEqual(files, set(sync.PUBLIC_FILES) | {"SOURCE.json"})
        self.assertFalse(any(b"PRIVATE_FIXTURE" in path.read_bytes() for path in self.engine.rglob("*") if path.is_file()))

    def test_committed_update_requires_new_pin_and_sync(self):
        sync.sync_engine(self.source, self.engine)
        original = json.loads((self.engine / "SOURCE.json").read_text())["upstream_commit"]
        self.shared("references/domains.json").write_text('{"new":"public domain"}\n')
        self.git("add", "skills/trurl/references/domains.json")
        self.commit()
        with self.assertRaisesRegex(ValueError, "Engine differs"):
            sync.sync_engine(self.source, self.engine, check=True)
        sync.sync_engine(self.source, self.engine)
        updated = json.loads((self.engine / "SOURCE.json").read_text())["upstream_commit"]
        self.assertNotEqual(original, updated)
        self.assertEqual(updated, self.git("rev-parse", "HEAD"))

    def test_engine_extras_and_symlinks_require_review(self):
        sync.sync_engine(self.source, self.engine)
        private = self.engine / "profile.json"
        private.write_text("Private-looking state must not be silently retained")
        with self.assertRaisesRegex(ValueError, "Unexpected engine files"):
            sync.sync_engine(self.source, self.engine, check=True)
        private.unlink()
        shared = self.shared("scripts/diversity.py")
        shared.unlink()
        shared.symlink_to(self.shared("scripts/graph.py"))
        self.git("add", "skills/trurl/scripts/diversity.py")
        self.commit()
        with self.assertRaisesRegex(ValueError, "Symlink not allowed"):
            sync.sync_engine(self.source, self.engine)

    def test_new_planner_dependencies_or_boundary_changes_require_review(self):
        with self.assertRaisesRegex(ValueError, "unapproved import: os"):
            sync._planner(PLANNER.replace("return math.sqrt(value)", "return os.getcwd()"))
        with self.assertRaisesRegex(ValueError, "extraction boundary"):
            sync._planner(PLANNER.replace("def initialize(root):", "def renamed_installer(root):"))


class BundledEngineTest(unittest.TestCase):
    """Run the actual vendored dependency graph against disposable synthetic state."""

    def test_bundled_references_show_native_calls_without_standalone_commands(self):
        for base in (ROOT / "engine", ROOT / "skills/trurl"):
            for reference in (base / "references").glob("*.md"):
                text = reference.read_text()
                self.assertNotRegex(text, r"\bpython(?:3(?:\.\d+)?)?\s+|`[^`\n]*\.py\s+|```(?:sh|bash|shell|console)\b|`--", reference)
            weekly = (base / "references/weekly-delivery.md").read_text()
            onboarding = (base / "references/onboarding.md").read_text()
            self.assertIn("one-off only", weekly)
            self.assertIn("one-off only", onboarding)
            self.assertNotIn("no Sundays", weekly)
        self.assertTrue(sync.check_bundled()["valid"])

    def test_references_resolve_within_hermes_plugin(self):
        for reference in (ROOT / "engine/references").glob("*.md"):
            text = reference.read_text()
            self.assertTrue(text.startswith(sync.REFERENCE_NOTE), reference.name)
            for value in re.findall(r"\[[^\]]*\]\(([^)]+)\)", text):
                parsed = urlsplit(value.strip().strip("<>"))
                if parsed.scheme or parsed.netloc or not parsed.path:
                    continue
                target = (reference.parent / unquote(parsed.path)).resolve()
                self.assertIn(ROOT, target.parents, (reference.name, value))
                self.assertTrue(target.is_file(), (reference.name, value))

    def test_projected_references_and_assets_stay_inside_skill(self):
        skill = ROOT / "skills/trurl"
        for name in sync.SKILL_PROJECTION_FILES:
            projected = skill / name
            self.assertTrue(projected.is_file(), name)
            if not name.endswith(".md"):
                continue
            text = projected.read_text()
            self.assertTrue(text.startswith(sync.REFERENCE_NOTE), name)
            self.assertNotIn("../../skills/trurl/SKILL.md", text)
            for value in re.findall(r"\[[^\]]*\]\(([^)]+)\)", text):
                parsed = urlsplit(value.strip().strip("<>"))
                if parsed.scheme or parsed.netloc or not parsed.path:
                    continue
                target = (projected.parent / unquote(parsed.path)).resolve()
                self.assertIn(skill, target.parents, (name, value))
                self.assertTrue(target.is_file(), (name, value))

    def test_new_helpers_and_atlas_work_from_copied_engine_without_location_or_installer(self):
        with TemporaryDirectory() as temporary:
            base = Path(temporary)
            installed = base / "engine"
            shutil.copytree(ROOT / "engine", installed, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            scripts = installed / "scripts"
            before = {str(path.relative_to(installed)): path.read_bytes() for path in installed.rglob("*") if path.is_file()}
            planner = runpy.run_path(str(scripts / "curation.py"))
            self.assertNotIn("initialize", planner)
            self.assertNotIn("install_skill", planner)
            self.assertFalse((scripts / "location.py").exists())
            profile = planner["generic_profile"]("Synthetic fixture", "UTC")
            profile["interests"] = [{"id": "maps", "topic": "Historical maps", "kind": "explicit",
                                     "source_provenance": ["fixture-user"], "knowledge_level": "unknown"}]
            profile["provenance"] = [{"id": "fixture-user", "kind": "user_statement", "locator": "synthetic://user"}]
            workspace = base / "snapshot"
            model = workspace / "model"
            model.mkdir(parents=True)
            (model / "profile.json").write_text(json.dumps(profile))
            (model / "graph.json").write_text(json.dumps({"schema_version": 1, "sources": [], "nodes": [], "edges": []}))
            (model / "feedback.jsonl").write_text(json.dumps({"id": "feedback", "user_words": "RAW_SYNTHETIC_FEEDBACK"}) + "\n")
            for name in ("recommendations", "batches"):
                (model / (name + ".jsonl")).write_text("")
            context = runpy.run_path(str(scripts / "context.py"))["prepare"](workspace)
            self.assertEqual([item["id"] for item in context["interests"]], ["maps"])
            self.assertEqual(context["brief"]["status"], "missing")
            self.assertNotIn("RAW_SYNTHETIC_FEEDBACK", json.dumps(context))
            hypotheses = runpy.run_path(str(scripts / "hypotheses.py"))
            self.assertTrue(hypotheses["maintenance_plan"](workspace, event="onboarding")["review_due"])
            anchors = runpy.run_path(str(scripts / "graph_anchors.py"))["sync_anchors"](workspace)
            self.assertEqual(anchors["counts"]["represented_interests"], 1)
            updates = runpy.run_path(str(scripts / "graph_updates.py"))
            review = updates["update_plan"](workspace, [], "synthetic-batch")
            self.assertTrue(review["review_due"])
            completed = updates["complete_update"](workspace, [], review, reason="No supported relationships in this synthetic empty research pool.")
            self.assertEqual(completed["receipt"]["outcome"], "no_supported_changes")
            self.assertFalse(updates["check_update"](workspace, [], "synthetic-batch")["review_due"])
            atlas = runpy.run_path(str(scripts / "atlas.py"))["export_atlas"](workspace)
            self.assertEqual(atlas["counts"]["stated"], 1)
            self.assertEqual(atlas["connections"]["edges"], 0)
            self.assertNotIn("__TRURL_ATLAS_DATA__", Path(atlas["output"]).read_text())
            self.assertEqual(profile, json.loads((model / "profile.json").read_text()))
            self.assertEqual(before, {str(path.relative_to(installed)): path.read_bytes() for path in installed.rglob("*") if path.is_file()})


if __name__ == "__main__":
    unittest.main()
