"""Check the explicit submission boundary using isolated synthetic sources."""

import hashlib
import json
from pathlib import Path
import runpy
from tempfile import TemporaryDirectory
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
        (self.root / "release-files.json").write_text(
            json.dumps({"format_version": 1, "files": self.names if names is None else names}))

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
            EXPORT["public_files"](self.root)
        self.manifest(["plugin.yaml"])
        with self.assertRaisesRegex(ValueError, "include itself"):
            EXPORT["public_files"](self.root)
        self.manifest()
        (self.root / "new_module.py").write_text("# Needs review before publication\n")
        with self.assertRaisesRegex(ValueError, "missing from release manifest.*new_module.py"):
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


if __name__ == "__main__":
    unittest.main()
