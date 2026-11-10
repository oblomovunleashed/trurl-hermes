"""Exercise SQLite-state adapters against real helpers with synthetic records."""

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest


ROOT = Path(__file__).resolve().parents[1]


class MemoryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Load the adapter without executing the registration module, which has
        # its own integration coverage and may be revised independently.
        package = "trurl_memory_under_test"
        spec = importlib.util.spec_from_file_location(
            package, ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
        module = importlib.util.module_from_spec(spec)
        sys.modules[package] = module
        cls.memory = importlib.import_module(package + ".memory")
        cls.curation = importlib.import_module(package + ".engine.scripts.curation")

    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.data = Path(temporary.name) / "data"
        self.state = {"profile": self.curation.generic_profile(), "history": [], "feedback": {}}
        self.state["profile"]["interests"] = [
            {"id": "a", "topic": "Synthetic author", "kind": "explicit", "scope": "persistent",
             "source_provenance": ["source-a"], "knowledge_level": "unknown"}]
        self.state["profile"]["provenance"] = [
            {"id": "source-a", "kind": "user_statement", "locator": "synthetic-message-a"}]

    def brief_draft(self):
        result, changed = self.memory.brief_tool({"action": "snapshot"}, self.state, self.data)
        self.assertFalse(changed)
        draft = result["snapshot"]
        draft.update(summary="The user has stated an interest in a synthetic author. Familiarity is unknown.",
                     source_refs=[{"path": "model/profile.json", "locator": "#/interests/0"}],
                     limits=["No independent preference evidence has been supplied."])
        for name, records in (("recommendations", self.state["history"]), ("feedback", self.state["feedback"])):
            if records:
                draft["source_refs"].append({"path": "model/" + name + ".jsonl", "locator": "line:1"})
        return draft

    def save_brief(self):
        return self.memory.brief_tool({"action": "save", "draft": self.brief_draft()}, self.state, self.data)

    def proposal(self):
        result, changed = self.memory.hypothesis_tool({"action": "snapshot"}, self.state, self.data)
        self.assertFalse(changed)
        draft = result["snapshot"]
        draft["taste_hypotheses"] = [{"id": "h", "topic": "Synthetic absurd humour",
                                     "reason": "This quality may explain part of the author's appeal.",
                                     "anchor_ids": ["a"], "evidence": [], "status": "testing"}]
        return draft

    def save_proposal(self):
        return self.memory.hypothesis_tool({"action": "save", "draft": self.proposal()}, self.state, self.data)

    def enjoyment(self, number, signal="enjoyed"):
        key = str(number)
        self.state["history"].append({"id": "r" + key, "title": "Synthetic work " + key,
                                      "creator": "Author", "canonical_url": "https://example.org/" + key,
                                      "recommended_at": "2026-11-06T15:00:00+00:00"})
        self.state["feedback"]["f" + key] = {
            "id": "f" + key, "recommendation_id": "r" + key, "signal": signal,
            "user_words": "I enjoyed this work.", "source_provenance": ["synthetic-message-" + key],
            "recorded_at": "2026-11-06T16:00:00+00:00"}
        return {"feedback_id": "f" + key, "effect": "support", "scope": "work",
                "reason": "The shared quality may be part of the experienced enjoyment."}

    def support(self):
        self.save_proposal()
        evidence = [self.enjoyment(1), self.enjoyment(2)]
        snapshot, _ = self.memory.hypothesis_tool({"action": "snapshot"}, self.state, self.data)
        draft = snapshot["snapshot"]
        draft["taste_hypotheses"][0]["evidence"] = evidence
        return self.memory.hypothesis_tool({"action": "save", "draft": draft}, self.state, self.data)

    def assert_clean_projection(self):
        self.assertFalse(any(self.data.glob(".trurl-memory-*")))
        self.assertFalse((self.data / "model").exists())

    def test_snapshots_are_read_only_independent_and_stable(self):
        before = copy.deepcopy(self.state)
        first, _ = self.memory.brief_tool({"action": "snapshot"}, self.state, self.data)
        second, _ = self.memory.brief_tool({"action": "snapshot"}, self.state, self.data)
        self.assertEqual(first, second)
        self.assertEqual({item["path"] for item in first["snapshot"]["sources"]}, set(self.memory.SOURCES))
        first["context"]["interests"].clear()
        self.assertEqual(self.state, before)
        self.assert_clean_projection()

    def test_brief_hash_and_lineage_survive_state_roundtrip(self):
        result, changed = self.save_brief()
        self.assertTrue(changed)
        info = self.memory.brief_info(self.state, self.data)
        self.assertEqual(info["status"], "fresh")
        self.assertEqual(info["version"], 1)
        self.assertEqual(info["file_sha256"], result["file_sha256"])
        expected = hashlib.sha256(self.memory._json(self.state["brief"]).encode()).hexdigest()
        self.assertEqual(info["file_sha256"], expected)
        self.state = json.loads(json.dumps(self.state))
        self.assertEqual(self.memory.brief_info(self.state, self.data), info)
        draft = self.brief_draft()
        self.assertEqual(draft["base_brief_sha256"], info["file_sha256"])
        result, _ = self.memory.brief_tool({"action": "save", "draft": draft}, self.state, self.data)
        self.assertEqual(result["version"], 2)
        self.assertEqual(self.state["brief"]["previous_sha256"], info["file_sha256"])
        self.assert_clean_projection()

    def test_stale_prose_is_withheld_and_stale_draft_is_rejected(self):
        self.save_brief()
        draft = self.brief_draft()
        self.state["profile"]["interests"][0]["topic"] = "A corrected author"
        info = self.memory.brief_info(self.state, self.data)
        self.assertEqual(info["status"], "stale")
        self.assertNotIn("summary", info)
        self.assertNotIn("version", info)
        self.assertNotIn("file_sha256", info)
        before = copy.deepcopy(self.state)
        with self.assertRaisesRegex(ValueError, "sources changed"):
            self.memory.brief_tool({"action": "save", "draft": draft}, self.state, self.data)
        self.assertEqual(self.state, before)
        self.assert_clean_projection()

    def test_history_and_feedback_each_invalidate_brief(self):
        for source in ("history", "feedback"):
            with self.subTest(source=source):
                self.save_brief()
                if source == "history":
                    self.state[source].append({"id": "suggestion", "title": "A suggestion"})
                else:
                    self.state[source]["reaction"] = {"id": "reaction", "signal": "would_try"}
                self.assertEqual(self.memory.brief_info(self.state, self.data)["status"], "stale")
        self.assert_clean_projection()

    def test_invalidated_brief_discards_prose_and_keeps_lineage(self):
        self.save_brief()
        before = self.memory.brief_info(self.state, self.data)["file_sha256"]
        result, changed = self.memory.brief_tool(
            {"action": "invalidate", "reason": "source_forgotten"}, self.state, self.data)
        self.assertTrue(changed)
        self.assertEqual(result["status"], "invalidated")
        self.assertNotIn("summary", self.state["brief"])
        self.assertEqual(self.state["brief"]["previous_sha256"], before)
        self.assertEqual(self.memory.brief_info(self.state, self.data)["status"], "stale")
        self.assert_clean_projection()

    def test_arbitrary_sources_and_extra_snapshot_paths_are_rejected(self):
        draft = self.brief_draft()
        draft["sources"].append({"path": "model/other.json", "sha256": "0" * 64})
        with self.assertRaisesRegex(ValueError, "Use only"):
            self.memory.brief_tool({"action": "save", "draft": draft}, self.state, self.data)
        with self.assertRaisesRegex(ValueError, "Unsupported"):
            self.memory.brief_tool({"action": "snapshot", "extra_sources": ["model/other.json"]},
                                   self.state, self.data)
        self.state["brief"] = {"sources": [{"path": "/etc/passwd"}], "summary": "Never expose this"}
        result = self.memory.brief_info(self.state, self.data)
        self.assertEqual(result["status"], "invalid")
        self.assertNotIn("summary", result)
        self.assert_clean_projection()

    def test_hypotheses_keep_actual_anchors_and_require_fresh_snapshot(self):
        before = copy.deepcopy(self.state["profile"])
        draft = self.proposal()
        result, changed = self.memory.hypothesis_tool({"action": "save", "draft": draft}, self.state, self.data)
        self.assertTrue(changed)
        self.assertEqual(result["hypotheses"][0]["status"], "testing")
        self.assertEqual(self.state["profile"]["interests"], before["interests"])
        self.assertEqual(self.state["profile"]["taste"], before["taste"])
        self.assertEqual(self.state["profile"]["delivery"], before["delivery"])
        with self.assertRaisesRegex(ValueError, "sources changed"):
            self.memory.hypothesis_tool({"action": "save", "draft": draft}, self.state, self.data)
        snapshot, _ = self.memory.hypothesis_tool({"action": "snapshot"}, self.state, self.data)
        snapshot["snapshot"]["taste_hypotheses"] = [
            {"id": "invented", "topic": "Invented taste", "reason": "No actual anchor",
             "anchor_ids": ["not-a-real-anchor"]}]
        with self.assertRaisesRegex(ValueError, "actual|current personal anchors"):
            self.memory.hypothesis_tool({"action": "save", "draft": snapshot["snapshot"]}, self.state, self.data)
        self.assert_clean_projection()

    def test_experienced_enjoyment_promotes_and_correction_retracts(self):
        result, _ = self.support()
        self.assertEqual(result["hypotheses"][0]["status"], "supported")
        self.assertEqual(self.state["profile"]["interests"][-1]["kind"], "inferred")
        self.assertFalse(self.memory.reconcile(self.state, self.data))
        self.state["feedback"]["f1"]["user_words"] = "That reaction was for a different work."
        self.assertTrue(self.memory.reconcile(self.state, self.data))
        self.assertEqual(len(self.state["profile"]["interests"]), 1)
        self.assertEqual(self.state["profile"]["taste_hypotheses"][0]["status"], "stale")
        self.assert_clean_projection()

    def test_first_impression_or_completion_cannot_support_taste(self):
        self.save_proposal()
        evidence = self.enjoyment(1, signal="would_try")
        for signal in ("would_try", "completed", "clicked"):
            with self.subTest(signal=signal):
                self.state["feedback"]["f1"]["signal"] = signal
                snapshot, _ = self.memory.hypothesis_tool({"action": "snapshot"}, self.state, self.data)
                snapshot["snapshot"]["taste_hypotheses"][0]["evidence"] = [evidence]
                before = copy.deepcopy(self.state)
                with self.assertRaises(ValueError):
                    self.memory.hypothesis_tool({"action": "save", "draft": snapshot["snapshot"]}, self.state, self.data)
                self.assertEqual(self.state, before)
        self.assert_clean_projection()

    def test_atlas_uses_brand_template_escapes_data_and_preserves_state(self):
        self.state["profile"]["interests"][0]["topic"] = '</script><script>alert("x")</script>'
        self.save_proposal()
        before = copy.deepcopy(self.state)
        result, changed = self.memory.atlas_tool({}, self.state, self.data)
        self.assertTrue(changed)
        self.assertEqual({key: self.state[key] for key in before}, before)
        self.assertEqual(len(self.state["graph"]["nodes"]), 1)
        self.assertEqual(self.state["graph"]["edges"], [])
        self.assertEqual(self.state["graph"]["sources"], [])
        self.assertFalse(self.memory.atlas_tool({}, self.state, self.data)[1])
        output = Path(result["output"])
        self.assertEqual(output, self.data.resolve() / "taste-atlas.html")
        text = output.read_text()
        self.assertNotIn('</script><script>alert("x")</script>', text)
        self.assertIn(r'\u003c/script\u003e', text)
        self.assertNotIn("__TRURL_ATLAS_DATA__", text)
        self.assertEqual(result["counts"]["stated"], 1)
        self.assertEqual({path.resolve() for path in self.data.iterdir()}, {output})
        self.assert_clean_projection()


if __name__ == "__main__":
    unittest.main()
