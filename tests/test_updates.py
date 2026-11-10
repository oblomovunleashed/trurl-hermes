"""Exercise the updated native workflow with synthetic, persistent profiles."""

from copy import deepcopy
from contextlib import closing
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
from tempfile import TemporaryDirectory
import unittest

from test_workflow import FakeContext, candidate, finish_hypothesis_review, finish_graph_review

ROOT = Path(__file__).resolve().parents[1]


class UpdateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("trurl_updates_test", ROOT / "__init__.py",
                                                      submodule_search_locations=[str(ROOT)])
        cls.plugin = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = cls.plugin
        spec.loader.exec_module(cls.plugin)

    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.data = Path(temp.name) / "profile"
        self.ctx = self.open()

    def open(self):
        ctx = FakeContext(self.data)
        self.plugin.register(ctx)
        return ctx

    def call(self, name, **args):
        result = self.ctx.call(name, **args)
        if name == "trurl_select" and result.get("error", "").startswith("Graph update is pending"):
            finish_graph_review(self.ctx, args["plan_id"], args["candidates"])
            result = self.ctx.call(name, **args)
        self.assertNotIn("error", result, result)
        return result

    def state(self):
        with closing(sqlite3.connect(self.data / "trurl.sqlite3")) as db:
            return json.loads(db.execute("SELECT data FROM curation WHERE id=1").fetchone()[0])

    def store(self, state):
        with closing(sqlite3.connect(self.data / "trurl.sqlite3")) as db, db:
            db.execute("UPDATE curation SET data=? WHERE id=1", (json.dumps(state),))

    def plan(self, **more):
        args = dict(request="Something to read from my interests", categories=["book"], count=1,
                    mode="personal", scope="broad")
        args.update(more)
        return finish_hypothesis_review(self.ctx, self.call("trurl_plan", **args))

    def save_brief(self):
        snap = self.call("trurl_brief", action="snapshot")
        draft = snap["snapshot"]
        draft.update(summary="The person is interested in tea. Familiarity with individual works remains unknown.",
                     source_refs=[{"path": "model/profile.json", "locator": "#/interests/0"}], limits=["Synthetic fixture."])
        return self.call("trurl_brief", action="save", draft=draft)

    def proposal(self):
        taste = self.call("trurl_taste", interests=["Tea"])
        draft = self.call("trurl_hypotheses", action="snapshot")["snapshot"]
        draft["taste_hypotheses"] = [{"id": "tea-ritual", "topic": "Ritual and material culture",
                                      "reason": "The practice of tea may be part of its appeal.",
                                      "anchor_ids": [taste["profile"]["interests"][0]["id"]]}]
        self.call("trurl_hypotheses", action="save", draft=draft)

    def enjoy_hypothesis_work(self, number):
        plan = self.plan()
        self.assertIn("tea-ritual", [entry.get("hypothesis_id") for entry in plan["discovery"]["briefs"]])
        item = candidate("tea-ritual-" + str(number), "book")
        item.update(hypothesis_id="tea-ritual", lane="adjacent", personal_connection={
            "basis": "explicit_interest", "anchor": self.state()["profile"]["interests"][0]["id"],
            "explanation": "An investigation of the rituals around tea."})
        selected = self.call("trurl_select", plan_id=plan["plan_id"], candidates=[item], commit=True)
        rec_id = selected["picks"][0]["recommendation_id"]
        self.call("trurl_feedback", recommendation_id=rec_id, reaction="liked", note="I read and enjoyed this work.",
                  source_provenance=["synthetic-user-message-" + str(number)])
        return rec_id

    def test_first_inspection_persists_and_additive_brain_dump_survives_reopen(self):
        first = self.call("trurl_taste")
        self.assertTrue(self.data.joinpath("trurl.sqlite3").exists())
        self.assertTrue(first["profile"]["delivery"]["weekly"]["onboarding_default_pending"])
        first = self.call("trurl_taste", brain_dump="Tea, Russian fiction and cooking.",
                          interests=["Tea", "Russian fiction"], categories=["book", "film"])
        self.call("trurl_taste", interests=["Cooking", "Tea"])
        self.ctx = self.open()
        result = self.call("trurl_taste")
        self.assertEqual(result["interests"], ["Tea", "Russian fiction", "Cooking"])
        self.assertEqual(result["categories"], ["book", "film"])
        self.assertFalse(result["weekly_enabled"])
        self.assertIn("Tea, Russian fiction and cooking.", str(result["profile"]["provenance"]))
        self.assertTrue(all(item["knowledge_level"] == "unknown" for item in result["profile"]["interests"]))
        removed = first["profile"]["interests"][1]["id"]
        self.call("trurl_taste", remove_interest_ids=[removed])
        self.assertEqual(self.call("trurl_taste")["interests"], ["Tea", "Cooking"])

    def test_source_choices_are_separate_and_permission_alone_is_not_availability(self):
        self.call("trurl_taste", context_sources={"agent_memory": {"permission": "allowed", "scope": "Reading interests"}})
        self.assertIn("error", self.ctx.call("trurl_taste", source="agent_memory", interests=["Tea"]))
        self.call("trurl_taste", context_sources={"agent_memory": {"availability": "partial"}})
        result = self.call("trurl_taste", source="agent_memory", source_note="Available host memory summary; original messages unavailable",
                           interests=["Tea"])
        self.assertEqual(result["profile"]["interests"][0]["kind"], "inferred")
        self.assertIn("error", self.ctx.call("trurl_taste", source="past_chats", interests=["Fiction"]))
        self.assertEqual(result["profile"]["onboarding"]["context_sources"]["past_chats"]["permission"], "not_asked")
        result = self.call("trurl_taste", interests=["Tea"])
        self.assertEqual(result["profile"]["interests"][0]["kind"], "explicit")

    def test_imported_evidence_cannot_change_direct_user_choices(self):
        taste = self.call("trurl_taste", interests=["Tea"], categories=["book"])
        interest_id = taste["profile"]["interests"][0]["id"]
        changes = [
            {"categories": ["film"]},
            {"remove_interest_ids": [interest_id]},
            {"weekly_enabled": False},
            {"replace_interests": True, "interests": ["Cooking"]},
            {"context_sources": {"past_chats": {"permission": "allowed", "scope": "Everything", "availability": "available"}}},
        ]
        for source in ("agent_memory", "past_chats"):
            for permission in ("declined", "allowed"):
                self.call("trurl_taste", context_sources={source: {
                    "permission": permission, "scope": "Reading interests", "availability": "available"}})
                for change in changes:
                    with self.subTest(source=source, permission=permission, change=change):
                        before = self.state()
                        result = self.ctx.call("trurl_taste", source=source,
                                               source_note="Synthetic imported evidence", **change)
                        self.assertIn("error", result)
                        self.assertEqual(self.state(), before)

    def test_imported_evidence_requires_an_actual_source_locator(self):
        for source in ("agent_memory", "past_chats"):
            self.call("trurl_taste", context_sources={source: {
                "permission": "allowed", "scope": "Reading interests", "availability": "available"}})
            for note in (None, "", "   "):
                with self.subTest(source=source, source_note=note):
                    before = self.state()
                    args = {"source": source, "interests": ["Imported " + source]}
                    if note is not None:
                        args["source_note"] = note
                    self.assertIn("error", self.ctx.call("trurl_taste", **args))
                    self.assertEqual(self.state(), before)
            result = self.call("trurl_taste", source=source, interests=["Imported " + source],
                               source_note="Synthetic source: " + source)
            record = next(item for item in result["profile"]["provenance"] if item["kind"] == source)
            self.assertEqual(record["locator"], "Synthetic source: " + source)

    def test_brief_is_recorded_at_planning_and_corrections_make_pending_plan_stale(self):
        self.call("trurl_taste", interests=["Tea"])
        saved = self.save_brief()
        plan = self.plan()
        self.assertEqual(plan["curators_brief"]["file_sha256"], saved["file_sha256"])
        item = candidate("tea-book", "book")
        selected = self.call("trurl_select", plan_id=plan["plan_id"], candidates=[item], commit=True)
        batch = self.state()["batches"][plan["plan_id"]]
        self.assertEqual(batch["curators_brief"]["version"], 1)
        self.assertNotIn("summary", batch["curators_brief"])
        self.assertEqual(batch["candidate_pool"], [item])
        self.assertEqual(batch["selection_plan"]["seed"], batch["selection_seed"])
        self.assertEqual(self.state()["history"][0]["batch_id"], plan["plan_id"])
        self.assertEqual(self.state()["history"][0]["engagement"], "unknown")
        pending = self.plan()
        self.assertEqual(pending["curators_brief"]["status"], "stale")
        self.assertNotIn("summary", pending["curators_brief"])
        self.call("trurl_taste", interests=["Cooking"])
        self.assertIn("error", self.ctx.call("trurl_select", plan_id=pending["plan_id"], candidates=[candidate("other", "book")], commit=True))
        retry = self.call("trurl_select", plan_id=plan["plan_id"], candidates=[], commit=True)
        self.assertEqual(retry["picks"], selected["picks"])
        self.assertEqual(len(self.state()["history"]), 1)

    def test_brief_invalidation_rejects_pending_selection_without_taste_change(self):
        self.call("trurl_taste", interests=["Tea"])
        self.save_brief()
        plan = self.plan()
        profile = deepcopy(self.state()["profile"])
        self.call("trurl_brief", action="invalidate", reason="source_corrected")
        self.assertEqual(self.state()["profile"], profile)
        before = self.state()
        result = self.ctx.call("trurl_select", plan_id=plan["plan_id"],
                               candidates=[candidate("pending-tea", "book")], commit=True)
        self.assertIn("error", result)
        self.assertEqual(self.state(), before)

    def test_fallback_plan_is_stale_when_brief_becomes_available(self):
        self.call("trurl_taste", interests=["Tea"])
        plan = self.plan()
        self.assertEqual(plan["curators_brief"]["status"], "missing")
        self.save_brief()
        before = self.state()
        self.assertIn("error", self.ctx.call("trurl_select", plan_id=plan["plan_id"],
                                             candidates=[candidate("pending-tea", "book")], commit=True))
        self.assertEqual(self.state(), before)

    def test_fallback_plan_is_stale_when_invalidated_evidence_changes_again(self):
        self.call("trurl_taste", interests=["Tea"])
        self.save_brief()
        self.call("trurl_brief", action="invalidate", reason="source_corrected")
        plan = self.plan()
        self.assertEqual(plan["curators_brief"]["status"], "stale")
        self.call("trurl_brief", action="invalidate", reason="source_forgotten")
        before = self.state()
        self.assertIn("error", self.ctx.call("trurl_select", plan_id=plan["plan_id"],
                                             candidates=[candidate("pending-tea", "book")], commit=True))
        self.assertEqual(self.state(), before)

    def test_legacy_records_migrate_without_losing_choices_or_claiming_native_delivery(self):
        self.call("trurl_taste", interests=["Tea"], categories=["book"])
        state = self.state()
        state.pop("schema_version")
        state["profile"]["delivery"]["weekly"] = {"enabled": True, "day": "sunday", "time": "20:00", "timezone": "Europe/London"}
        state["history"] = [{"id": "old-pick", "title": "Old pick", "creator": "Author", "canonical_url": "https://example.org/old", "status": "recommended"}]
        state["feedback"] = {"old-feedback": {"reaction": "liked", "recommendation_id": "old-pick", "note": "I enjoyed it."}}
        self.store(state)
        self.ctx = self.open()
        taste = self.call("trurl_taste")
        migrated = self.state()
        self.assertEqual(taste["interests"], ["Tea"])
        self.assertEqual(taste["categories"], ["book"])
        self.assertEqual(taste["recommendation_count"], 1)
        self.assertTrue(taste["profile"]["delivery"]["weekly"]["legacy_consent_enabled"])
        self.assertFalse(taste["weekly_enabled"])
        self.assertFalse(taste["profile"]["delivery"]["weekly"]["onboarding_default_pending"])
        self.assertEqual(migrated["feedback"]["old-feedback"]["signal"], "liked")
        self.assertEqual(migrated["feedback"]["old-feedback"]["id"], "old-feedback")

    def test_native_hypothesis_feedback_grows_and_corrects_taste(self):
        self.proposal()
        for number in (1, 2):
            rec_id = self.enjoy_hypothesis_work(number)
        profile = self.call("trurl_taste")["profile"]
        self.assertEqual(profile["taste_hypotheses"][0]["status"], "supported")
        self.assertTrue(any(item.get("hypothesis_id") == "tea-ritual" for item in profile["interests"]))
        self.call("trurl_feedback", recommendation_id=rec_id, reaction="rejected_interest", hypothesis_id="tea-ritual",
                  note="I do not want ritual or material culture as a general interest.")
        profile = self.call("trurl_taste")["profile"]
        self.assertEqual(profile["taste_hypotheses"][0]["status"], "rejected")
        self.assertFalse(any(item.get("hypothesis_id") == "tea-ritual" for item in profile["interests"]))

    def assert_explicit_anchor_survives(self, via_feedback=False):
        self.proposal()
        for number in (1, 2):
            rec_id = self.enjoy_hypothesis_work(number)
        inferred = self.call("trurl_taste")["profile"]
        self.assertTrue(any(item.get("hypothesis_id") == "tea-ritual" for item in inferred["interests"]))
        if via_feedback:
            result = self.call("trurl_feedback", recommendation_id=rec_id, reaction="more_like_this",
                               interest="Ritual and material culture", note="This is also an interest in its own right.")
            self.assertTrue(result["interest_added"])
            stated = self.call("trurl_taste")["profile"]
        else:
            stated = self.call("trurl_taste", interests=["Ritual and material culture"])["profile"]
        anchors = [item for item in stated["interests"] if item["topic"] == "Ritual and material culture"
                   and item["kind"] == "explicit" and not item.get("hypothesis_id")]
        self.assertEqual(len(anchors), 1)
        explicit = deepcopy(anchors[0])
        self.assertFalse(explicit["id"].startswith("hypothesis:"))
        self.call("trurl_feedback", recommendation_id=rec_id, reaction="rejected_interest", hypothesis_id="tea-ritual",
                  note="Reject this tentative connection to tea; retain my separately stated interest.")
        self.ctx = self.open()
        profile = self.call("trurl_taste")["profile"]
        self.assertEqual(profile["taste_hypotheses"][0]["status"], "rejected")
        self.assertFalse(any(item.get("hypothesis_id") == "tea-ritual" for item in profile["interests"]))
        self.assertIn(explicit, profile["interests"])

    def test_explicit_anchor_survives_rejection_of_matching_hypothesis(self):
        self.assert_explicit_anchor_survives()

    def test_more_like_this_creates_independent_anchor_that_survives_hypothesis_rejection(self):
        self.assert_explicit_anchor_survives(via_feedback=True)

    def test_malformed_hypothesis_ids_return_errors_without_mutating_state(self):
        self.call("trurl_taste", interests=["Tea"])
        plan = self.plan()
        for invalid in (["tea-ritual"], {"id": "tea-ritual"}):
            with self.subTest(operation="selection", value=invalid):
                item = candidate("invalid-hypothesis", "book")
                item["hypothesis_id"] = invalid
                before = self.state()
                self.assertIn("error", self.ctx.call("trurl_select", plan_id=plan["plan_id"], candidates=[item], commit=True))
                self.assertEqual(self.state(), before)
        selected = self.call("trurl_select", plan_id=plan["plan_id"], candidates=[candidate("valid-tea", "book")], commit=True)
        rec_id = selected["picks"][0]["recommendation_id"]
        for invalid in (["tea-ritual"], {"id": "tea-ritual"}):
            with self.subTest(operation="feedback", value=invalid):
                before = self.state()
                self.assertIn("error", self.ctx.call("trurl_feedback", recommendation_id=rec_id, reaction="liked",
                                                     note="I enjoyed this.", hypothesis_id=invalid))
                self.assertEqual(self.state(), before)

    def test_missing_helper_draft_metadata_returns_errors_without_mutating_state(self):
        self.call("trurl_taste", interests=["Tea"])
        for tool in ("trurl_brief", "trurl_hypotheses"):
            for args in ({"action": "save"}, {"action": "save", "draft": {}}):
                with self.subTest(tool=tool, args=args):
                    before = self.state()
                    self.assertIn("error", self.ctx.call(tool, **args))
                    self.assertEqual(self.state(), before)

    def test_atlas_is_an_explicit_export_and_never_rewrites_taste(self):
        self.call("trurl_taste", interests=["Tea </script><script>alert(1)</script>"])
        before = deepcopy(self.state())
        self.assertFalse((self.data / "taste-atlas.html").exists())
        result = self.call("trurl_atlas")
        self.assertEqual(Path(result["output"]).resolve(), (self.data / "taste-atlas.html").resolve())
        self.assertEqual(self.state(), before)
        self.assertIn("\\u003c/script\\u003e", Path(result["output"]).read_text())
        self.assertFalse(any(self.data.glob(".trurl-memory-*")))


if __name__ == "__main__":
    unittest.main()
