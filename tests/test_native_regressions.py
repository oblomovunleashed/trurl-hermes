"""Regression cases first discovered through the real Hermes tool registry."""

import importlib.util
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest


ROOT = Path(__file__).resolve().parents[1]


class NativeRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location(
            "trurl_native_regression", ROOT / "__init__.py",
            submodule_search_locations=[str(ROOT)])
        package = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = package
        spec.loader.exec_module(package)
        cls.invoke = staticmethod(package.invoke)

    def test_combined_onboarding_keeps_current_user_provenance(self):
        for permission, availability, scope in (
                ("declined", "unavailable", ""),
                ("allowed", "available", "Relevant interests only")):
            with self.subTest(permission=permission), TemporaryDirectory() as directory:
                words = "I like rivers. Please suggest a book or a podcast."
                result = json.loads(self.invoke(Path(directory), "trurl_taste", {
                    "interests": ["Rivers"], "categories": ["book", "podcast"],
                    "brain_dump": words,
                    "context_sources": {
                        name: {"permission": permission, "availability": availability, "scope": scope}
                        for name in ("agent_memory", "past_chats")},
                }))
                self.assertNotIn("error", result)
                self.assertEqual(result["interests"], ["Rivers"])
                self.assertEqual(result["categories"], ["book", "podcast"])
                profile = result["profile"]
                self.assertEqual(profile["interests"][0]["kind"], "explicit")
                evidence = profile["provenance"][0]
                self.assertEqual(evidence["kind"], "current_conversation")
                self.assertEqual(evidence["user_words"], words)
                self.assertNotIn("source_choice", evidence)
                for choice in profile["onboarding"]["context_sources"].values():
                    self.assertEqual(choice["permission"], permission)
                    self.assertEqual(choice["availability"], availability)


if __name__ == "__main__":
    unittest.main()
