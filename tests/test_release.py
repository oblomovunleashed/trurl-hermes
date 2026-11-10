"""Check the explicit submission boundary using isolated synthetic sources."""

import hashlib
import importlib.util
import json
from pathlib import Path
import runpy
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from zipfile import ZipFile


ROOT = Path(__file__).resolve().parents[1]
EXPORT = runpy.run_path(str(ROOT / "scripts/package_release.py"))


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.root = self.base / "source"
        self.root.mkdir()
        (self.root / "plugin.yaml").write_text('name: trurl\nversion: "0.3.1"\n')
        (self.root / "__init__.py").write_text('"""Synthetic plugin."""\n')
        self.names = ["plugin.yaml", "__init__.py", "release-files.json"]
        self.manifest()

    def manifest(self, names=None):
        names = self.names if names is None else names
        (self.root / "release-files.json").write_text(
            json.dumps({"format_version": 1, "files": names}))
        (self.root / "source-files.json").write_text(
            json.dumps({"format_version": 1, "files": sorted(set(names) | {"source-files.json"})}))

    def test_only_reviewed_payload_is_exported_without_git_or_private_runtime_data(self):
        for name in [".git/config", "model/profile.json", "profile/trurl.sqlite3", "runtime/demo.py",
                     "video-pack/frames.json", "__pycache__/__init__.pyc", ".env", "private-note.txt"]:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("synthetic-private-marker")
        result = EXPORT["package"](self.root, self.base / "export")
        release = Path(result["directory"])
        actual = {path.relative_to(release).as_posix() for path in release.rglob("*") if path.is_file()}
        self.assertEqual(actual, set(self.names))
        with ZipFile(result["archive"]) as archive:
            self.assertEqual(set(archive.namelist()), {"trurl/" + name for name in self.names})
            self.assertTrue(all(b"synthetic-private-marker" not in archive.read(name)
                                for name in archive.namelist()))

    def test_archives_are_reproducible_and_have_checked_sums(self):
        first = EXPORT["package"](self.root, self.base / "one")
        second = EXPORT["package"](self.root, self.base / "two")
        data = Path(first["archive"]).read_bytes()
        self.assertEqual(data, Path(second["archive"]).read_bytes())
        self.assertEqual(first["archive_sha256"], hashlib.sha256(data).hexdigest())
        self.assertEqual(Path(first["checksums"]).read_text(), first["archive_sha256"] + "  trurl.zip\n")
        with ZipFile(first["archive"]) as archive:
            self.assertTrue(all(info.date_time == (2026, 1, 1, 0, 0, 0) for info in archive.infolist()))
            self.assertTrue(all(info.external_attr >> 16 == 0o100644 for info in archive.infolist()))

    def test_manifest_cannot_opt_private_or_generated_files_back_into_the_release(self):
        for name in [".git/config", "model/profile.json", ".env", "trurl.sqlite3-wal", "events.jsonl",
                     "runtime/run-agent.py", "video-pack/demo.png", "__pycache__/module.pyc"]:
            with self.subTest(name=name):
                path = self.root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("private")
                self.manifest(self.names + [name])
                with self.assertRaisesRegex(ValueError, "Private/generated"):
                    EXPORT["package"](self.root, self.base / "export")
                self.assertFalse((self.base / "export").exists())

    def test_invalid_missing_duplicate_or_unlisted_public_files_fail_before_export(self):
        for name in ["../outside.py", "/absolute.py", "C:/outside.py", "./plugin.yaml", "folder\\file.py", "absent.py"]:
            with self.subTest(name=name):
                self.manifest(self.names + [name])
                with self.assertRaisesRegex(ValueError, "Invalid|Missing"):
                    EXPORT["package"](self.root, self.base / "export")
        self.manifest(self.names + ["plugin.yaml"])
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            EXPORT["release_files"](self.root)
        self.manifest(["plugin.yaml"])
        (self.root / "source-files.json").write_text(
            json.dumps({"format_version": 1, "files": ["plugin.yaml"]}))
        with self.assertRaisesRegex(ValueError, "include itself"):
            EXPORT["public_files"](self.root)
        self.manifest()
        (self.root / "new_module.py").write_text("# Needs review before publication\n")
        with self.assertRaisesRegex(ValueError, "missing from source manifest.*new_module.py"):
            EXPORT["check_source"](self.root)
        self.assertFalse((self.base / "export").exists())

    def test_symlinked_manifest_file_and_directory_are_rejected(self):
        outside = self.base / "outside.txt"
        outside.write_text("private")
        (self.root / "leak.txt").symlink_to(outside)
        self.manifest(self.names + ["leak.txt"])
        with self.assertRaisesRegex(ValueError, "Symlink"):
            EXPORT["public_files"](self.root)

        (self.root / "linked").symlink_to(self.base, target_is_directory=True)
        self.manifest(self.names + ["linked/outside.txt"])
        with self.assertRaisesRegex(ValueError, "Symlink"):
            EXPORT["public_files"](self.root)
        (self.root / "release-files.json").unlink()
        (self.root / "release-files.json").symlink_to(outside)
        with self.assertRaisesRegex(ValueError, "Symlink"):
            EXPORT["public_files"](self.root)

    def test_existing_or_in_source_destinations_are_never_overwritten(self):
        destination = self.base / "existing"
        destination.mkdir()
        marker = destination / "keep.txt"
        marker.write_text("keep")
        with self.assertRaises(FileExistsError):
            EXPORT["package"](self.root, destination)
        self.assertEqual(marker.read_text(), "keep")
        with self.assertRaisesRegex(ValueError, "outside the plugin source"):
            EXPORT["package"](self.root, self.root / "release")
        link = self.base / "linked-output"
        link.symlink_to(destination, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlink"):
            EXPORT["package"](self.root, link)

    def test_real_manifest_contains_all_public_source_and_itself(self):
        result = EXPORT["check_source"](ROOT)
        self.assertTrue(result["valid"])
        files = dict(EXPORT["public_files"](ROOT))
        self.assertIn("scripts/package_release.py", files)
        self.assertIn("tests/test_release.py", files)
        self.assertIn("assets/trurl-constructor.png", files)
        self.assertIn("engine/SOURCE.json", files)

    def test_runtime_cannot_include_repository_material_even_if_source_reviewed(self):
        for name in ["tests/test_plugin.py", "scripts/maintainer.py", "assets/screenshot.jpg",
                     ".github/workflows/ci.yml", "README.md", "source-files.json"]:
            with self.subTest(name=name):
                path = self.root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("reviewed repository content")
                self.manifest(self.names + [name])
                with self.assertRaisesRegex(ValueError, "Repository-only"):
                    EXPORT["release_files"](self.root)

    def test_runtime_must_also_be_reviewed_in_source_boundary(self):
        (self.root / "extra.py").write_text("# Synthetic runtime module\n")
        (self.root / "release-files.json").write_text(
            json.dumps({"format_version": 1, "files": self.names + ["extra.py"]}))
        with self.assertRaisesRegex(ValueError, "Runtime file missing from source manifest"):
            EXPORT["release_files"](self.root)

    def test_complete_source_archive_is_explicit_and_not_the_install_default(self):
        (self.root / "README.md").write_text("# Synthetic source documentation\n")
        (self.root / "source-files.json").write_text(
            json.dumps({"format_version": 1,
                        "files": self.names + ["source-files.json", "README.md"]}))
        runtime = EXPORT["package"](self.root, self.base / "runtime-export")
        source = EXPORT["package"](self.root, self.base / "source-export", kind="source")
        self.assertEqual(Path(runtime["archive"]).name, "trurl.zip")
        self.assertEqual(Path(source["archive"]).name, "trurl-source.zip")
        with ZipFile(runtime["archive"]) as archive:
            self.assertNotIn("trurl/README.md", archive.namelist())
            self.assertNotIn("trurl/source-files.json", archive.namelist())
        with ZipFile(source["archive"]) as archive:
            self.assertIn("trurl/README.md", archive.namelist())
            self.assertIn("trurl/source-files.json", archive.namelist())

    def test_real_runtime_archive_loads_tools_and_renders_atlas_and_sunday_resources(self):
        result = EXPORT["package"](ROOT, self.base / "runtime-export")
        installed = self.base / "plugins"
        with ZipFile(result["archive"]) as archive:
            names = archive.namelist()
            self.assertFalse(any(name.startswith(("trurl/assets/", "trurl/tests/", "trurl/scripts/",
                                                 "trurl/.github/", "trurl/.git/", "trurl/examples/"))
                                 for name in names))
            self.assertNotIn("trurl/README.md", names)
            archive.extractall(installed)
        runtime = (installed / "trurl").resolve()
        package_name = "trurl_release_installed_test"
        self.addCleanup(lambda: [sys.modules.pop(name, None) for name in list(sys.modules)
                                 if name == package_name or name.startswith(package_name + ".")])
        spec = importlib.util.spec_from_file_location(
            package_name, runtime / "__init__.py", submodule_search_locations=[str(runtime)])
        plugin = importlib.util.module_from_spec(spec)
        sys.modules[package_name] = plugin
        spec.loader.exec_module(plugin)
        tools, skills = {}, []
        ctx = SimpleNamespace(
            state=SimpleNamespace(data_dir=self.base / "profile"),
            register_tool=lambda **tool: tools.update({tool["name"]: tool["handler"]}),
            register_skill=lambda name, path, **kwargs: skills.append(Path(path)),
        )
        plugin.register(ctx)
        self.assertEqual(len(tools), 10)
        self.assertTrue(all(path.is_file() and runtime in path.parents for path in skills))
        self.assertFalse(ctx.state.data_dir.exists())

        def call(name, **args):
            response = json.loads(tools[name](args))
            self.assertNotIn("error", response, response)
            return response

        call("trurl_taste", interests=["Synthetic river history"], categories=["book"],
             brain_dump="An isolated release-packaging regression fixture.")
        atlas = call("trurl_atlas")
        self.assertTrue(Path(atlas["output"]).is_file())
        self.assertIn("Synthetic river history", Path(atlas["output"]).read_text())
        plan = call("trurl_plan", request="A book about river history", mode="personal",
                    scope="focused", categories=["book"], edition_date="2026-11-15")
        candidate = {
            "id": "synthetic-river-book", "title": "Synthetic river book", "creator": "Fixture Author",
            "canonical_url": "https://example.org/river-book", "medium": "book", "lane": "familiar",
            "topics": ["river history"],
            "scores": dict.fromkeys(("depth", "novelty", "relevance", "surprise",
                                      "perspective_change", "entertainment"), 4),
            "credibility_passed": True, "constraints_passed": True,
            "verification": {"checked_at": "2026-11-10", "urls": ["https://example.org/river-book"]},
            "description": "Synthetic release fixture, not a real recommendation.",
        }
        review = call("trurl_graph", action="plan", plan_id=plan["plan_id"], candidates=[candidate])
        if review["review_due"]:
            call("trurl_graph", action="complete", plan_id=plan["plan_id"], candidates=[candidate],
                 review=review, reason="Fixture metadata supports no factual content relationships.")
        edition = call("trurl_select", plan_id=plan["plan_id"], candidates=[candidate], commit=True)
        self.assertTrue(Path(edition["edition_files"]["svg"]).is_file())
        self.assertIn("Synthetic river book", Path(edition["edition_files"]["svg"]).read_text())


if __name__ == "__main__":
    unittest.main()
