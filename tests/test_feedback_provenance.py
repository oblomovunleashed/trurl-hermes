"""Original feedback sources, rather than stored events, govern corroboration."""

from contextlib import closing
from copy import deepcopy
import importlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
from tempfile import TemporaryDirectory
import unittest

from test_workflow import FakeContext, candidate


ROOT = Path(__file__).resolve().parents[1]


class FeedbackProvenanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location(
            "trurl_feedback_provenance_test", ROOT / "__init__.py",
            submodule_search_locations=[str(ROOT)])
        cls.plugin = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = cls.plugin
        spec.loader.exec_module(cls.plugin)
        cls.memory = importlib.import_module(spec.name + ".memory")

    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.data = Path(temporary.name)
        self.ctx = FakeContext(self.data)
        self.plugin.register(self.ctx)
        profile = self.call("trurl_taste", interests=["Synthetic history"])["profile"]
        draft = self.call("trurl_hypotheses", action="snapshot")["snapshot"]
        draft["taste_hypotheses"] = [{
            "id": "h", "topic": "Synthetic histories of cities", "status": "testing", "evidence": [],
            "anchor_ids": [profile["interests"][0]["id"]],
            "reason": "Urban history may explain part of the stated historical interest.",
        }]
        self.call("trurl_hypotheses", action="save", draft=draft)
        state = self.state()
        state["history"] = [dict(candidate(identifier, "book"), id=identifier, hypothesis_id="h")
                            for identifier in ("r1", "r2")]
        self.store(state)

    def call(self, name, **args):
        result = self.ctx.call(name, **args)
        self.assertNotIn("error", result, result)
        return result

    def state(self):
        with closing(sqlite3.connect(self.data / "trurl.sqlite3")) as db:
            return json.loads(db.execute("SELECT data FROM curation WHERE id=1").fetchone()[0])

    def store(self, state):
        with closing(sqlite3.connect(self.data / "trurl.sqlite3")) as db, db:
            db.execute("UPDATE curation SET data=? WHERE id=1", (json.dumps(state),))

    def react(self, identifier, **extra):
        return self.call("trurl_feedback", recommendation_id=identifier, reaction="liked",
                         note="I enjoyed both books for their descriptions of cities.", **extra)

    def test_unlocated_reactions_do_not_invent_independent_sources(self):
        for identifier in ("r1", "r2"):
            self.react(identifier)
        state = self.state()
        hypothesis = state["profile"]["taste_hypotheses"][0]
        self.assertEqual((hypothesis["status"], hypothesis["supporting_work_count"]), ("testing", 1))
        self.assertFalse(any(item.get("hypothesis_id") == "h" for item in state["profile"]["interests"]))
        records = list(state["feedback"].values())
        self.assertEqual(records[0]["source_provenance"], records[1]["source_provenance"])
        self.assertEqual(len({record["event_locator"] for record in records}), 2)

    def test_distinct_identified_sources_can_support_the_hypothesis(self):
        for identifier in ("r1", "r2"):
            self.react(identifier, source_provenance=["synthetic-message-" + identifier])
        state = self.state()
        self.assertEqual(state["profile"]["taste_hypotheses"][0]["status"], "supported")
        self.assertTrue(any(item.get("hypothesis_id") == "h" for item in state["profile"]["interests"]))

    def test_reused_original_message_is_one_source_despite_capture_metadata(self):
        for identifier, captured in (("r1", "2026-11-09"), ("r2", "2026-11-10")):
            self.react(identifier, source_provenance=[{
                "kind": "user_statement", "thread_id": "synthetic-thread", "message_id": "same-message",
                "captured_at": captured,
            }])
        hypothesis = self.state()["profile"]["taste_hypotheses"][0]
        self.assertEqual((hypothesis["status"], hypothesis["supporting_work_count"]), ("testing", 1))

    def test_explicit_source_identity_distinguishes_events_and_retries(self):
        source = {"kind": "user_statement", "locator": "chat://synthetic/message-1", "captured_at": "2026-11-09"}
        first = self.react("r1", source_provenance=[source])
        self.assertNotIn("already_recorded", first)
        replay = self.react("r1", source_provenance=[dict(source, captured_at="2026-11-10")])
        self.assertTrue(replay["already_recorded"])
        second = self.react("r1", source_provenance=[dict(source, locator="chat://synthetic/message-2")])
        self.assertNotIn("already_recorded", second)
        self.assertEqual(len(self.state()["feedback"]), 2)

    def test_unlocated_explicit_confirmation_remains_valid(self):
        self.call("trurl_feedback", reaction="confirmed_interest", hypothesis_id="h", scope="topic",
                  target="Synthetic histories of cities", note="I enjoy histories of cities as a broader interest.")
        state = self.state()
        self.assertEqual(state["profile"]["taste_hypotheses"][0]["status"], "confirmed")
        learned = next(item for item in state["profile"]["interests"] if item.get("hypothesis_id") == "h")
        self.assertEqual(learned["kind"], "explicit")

    def test_invalid_or_nonpersonal_sources_are_rejected_without_writing(self):
        invalid = [[], [{}], [{"thread_id": "thread-with-no-message"}],
                   [{"kind": "assistant_suggestion", "id": "assistant"}],
                   [{"kind": "third_party", "locator": "https://example.org/review"}],
                   ["sqlite:feedback/stored-event"], [42], [" "]]
        for sources in invalid:
            with self.subTest(sources=sources):
                before = self.state()
                result = self.ctx.call("trurl_feedback", recommendation_id="r1", reaction="liked",
                                       note="I enjoyed this book.", source_provenance=sources)
                self.assertIn("error", result)
                self.assertEqual(self.state(), before)

    def legacy_supported_state(self, identified=False):
        state = self.state()
        state["schema_version"] = 2
        for identifier in ("r1", "r2"):
            key = "old-feedback-" + identifier
            source = ({"kind": "user_statement", "locator": "chat://synthetic/" + identifier}
                      if identified else {"kind": "user_feedback", "locator": "sqlite:feedback/" + key})
            state["feedback"][key] = {
                "id": key, "recommendation_id": identifier, "reaction": "liked", "signal": "liked",
                "hypothesis_id": "h", "scope": "item", "target": identifier,
                "user_words": "I enjoyed both books for their descriptions of cities.",
                "note": "I enjoyed both books for their descriptions of cities.", "profile_changes": [],
                "source_provenance": [source], "recorded_at": "2026-11-09T12:00:00+00:00",
            }
        snapshot, unused = self.memory.hypothesis_tool({"action": "snapshot"}, state, self.data)
        draft = snapshot["snapshot"]
        draft["taste_hypotheses"][0]["evidence"] = [
            {"feedback_id": key, "effect": "support", "scope": "work",
             "reason": "The shared historical quality may explain the stated enjoyment."}
            for key in state["feedback"]]
        self.memory.hypothesis_tool({"action": "save", "draft": draft}, state, self.data)
        self.assertEqual(state["profile"]["taste_hypotheses"][0]["status"], "supported")
        return state

    def test_version_two_generated_sources_migrate_and_false_support_is_invalidated(self):
        state = self.legacy_supported_state()
        original = deepcopy(state["feedback"])
        self.store(state)
        self.call("trurl_context")
        migrated = self.state()
        self.assertEqual(migrated["schema_version"], 3)
        self.assertEqual(migrated["profile"]["taste_hypotheses"][0]["status"], "stale")
        self.assertFalse(any(item.get("hypothesis_id") == "h" for item in migrated["profile"]["interests"]))
        for key, record in migrated["feedback"].items():
            self.assertEqual(record["event_locator"], "sqlite:feedback/" + key)
            self.assertEqual(record["source_provenance"][0]["id"], "unlocated-current-conversation")
            for field in ("id", "user_words", "note", "target", "recorded_at", "recommendation_id"):
                self.assertEqual(record[field], original[key][field])
        self.call("trurl_context")
        self.assertEqual(self.state(), migrated)

    def test_version_two_identified_sources_and_supported_interest_are_preserved(self):
        state = self.legacy_supported_state(identified=True)
        original = deepcopy(state["feedback"])
        self.store(state)
        self.call("trurl_context")
        migrated = self.state()
        self.assertEqual(migrated["schema_version"], 3)
        self.assertEqual(migrated["feedback"], original)
        self.assertEqual(migrated["profile"]["taste_hypotheses"][0]["status"], "supported")


if __name__ == "__main__":
    unittest.main()
