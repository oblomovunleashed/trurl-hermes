"""Native SQLite bridges retain real maintenance and graph completion gates."""
from copy import deepcopy
from contextlib import closing
import importlib.util
import json
from pathlib import Path
import re
import sqlite3
import sys
from tempfile import TemporaryDirectory
import unittest

from test_workflow import FakeContext, candidate, finish_hypothesis_review


ROOT = Path(__file__).resolve().parents[1]


class CurrentEngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("trurl_current_engine", ROOT / "__init__.py",
                                                     submodule_search_locations=[str(ROOT)])
        cls.plugin = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = cls.plugin
        spec.loader.exec_module(cls.plugin)

    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.data = Path(temp.name) / "active-profile"
        self.ctx = FakeContext(self.data)
        self.plugin.register(self.ctx)
        self.call("trurl_taste", interests=["Chinese tea", "Urban history"], categories=["book"],
                  context_sources={"agent_memory": {"permission": "declined"},
                                   "past_chats": {"permission": "declined"}})

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

    def plan(self, scope="focused"):
        return self.call("trurl_plan", request="One book connecting tea and urban history", count=1,
                         categories=["book"], mode="personal", scope=scope)

    def graph_review(self, plan, items, delta=None):
        review = self.call("trurl_graph", action="plan", plan_id=plan["plan_id"], candidates=items)
        return self.call("trurl_graph", action="complete", plan_id=plan["plan_id"], candidates=items,
                         review=review, delta=delta,
                         reason="Reviewed the synthetic publisher metadata and only its supported content claims.")

    def content_delta(self, item):
        nodes = self.call("trurl_graph", action="records", kind="nodes")["items"]
        topics = {node["label"]: node["id"] for node in nodes}
        return {"schema_version": 1,
                "sources": [{"id": "publisher-a", "kind": "publisher", "locator": item["canonical_url"],
                             "checked_at": "2026-11-09"}],
                "nodes": [{"id": "work-a", "type": "work", "label": item["title"],
                           "canonical_url": item["canonical_url"]}],
                "edges": [{"id": "edge-" + str(index), "source": "work-a", "target": topics[topic],
                           "relation": "about", "status": "verified", "confidence": .9,
                           "source_provenance": ["publisher-a"], "assertion_method": "host_verified",
                           "support_note": "Synthetic publisher describes this work's subject as " + topic + "."}
                          for index, topic in enumerate(("Chinese tea", "Urban history"))]}

    def test_broad_review_blocks_discovery_and_resumes_same_plan_and_seeds(self):
        before = self.state()["profile"]
        first = self.plan("broad")
        self.assertTrue(first["hypothesis_maintenance"]["review_due"])
        self.assertIsNone(first["discovery"])
        self.assertNotIn("hypothesis_maintenance", self.state())
        initial = deepcopy(self.state()["plans"][first["plan_id"]])
        self.assertIn("error", self.ctx.call("trurl_select", plan_id=first["plan_id"], candidates=[]))
        current = finish_hypothesis_review(self.ctx, first)
        self.assertEqual(current["plan_id"], first["plan_id"])
        self.assertEqual(current["weights"], first["weights"])
        saved = self.state()["plans"][first["plan_id"]]
        for key in ("plan", "selection_seed", "discovery_seed"):
            self.assertEqual(saved[key], initial[key])
        self.assertEqual(self.call("trurl_plan", plan_id=first["plan_id"])["discovery"], current["discovery"])
        self.assertEqual(self.state()["profile"], before)
        self.assertEqual(self.state()["hypothesis_maintenance"]["outcome"], "no_proposal")
        self.assertFalse(any(self.data.glob(".trurl-memory-*")))

    def test_saved_hypothesis_can_finish_review_without_resampling(self):
        first = self.plan("broad")
        draft = self.call("trurl_hypotheses", action="snapshot")["snapshot"]
        ids = [item["id"] for item in self.state()["profile"]["interests"]]
        draft["taste_hypotheses"] = [{"id": "tea-cities", "topic": "Tea and the history of cities",
                                      "anchor_ids": ids, "status": "testing", "evidence": [],
                                      "reason": "Tea supplies the cultural practice; history supplies the city question. "
                                                "A competing explanation is interest in each topic separately; test a joint work."}]
        self.call("trurl_hypotheses", action="save", draft=draft)
        self.call("trurl_hypotheses", action="maintenance-complete", plan_id=first["plan_id"],
                  review=first["hypothesis_maintenance"], outcome="saved", hypothesis_ids=["tea-cities"],
                  reason="Saved one testable possibility grounded in both original interests.")
        resumed = self.call("trurl_plan", plan_id=first["plan_id"])
        self.assertEqual(resumed["weights"], first["weights"])
        self.assertIsNotNone(resumed["discovery"])
        self.assertEqual(self.state()["profile"]["taste_hypotheses"][0]["status"], "testing")
        self.assertEqual(len(self.state()["profile"]["interests"]), 2)

    def test_new_original_evidence_invalidates_a_pending_review_and_resume(self):
        first = self.plan("broad")
        self.call("trurl_taste", interests=["Botanical illustration"])
        before = self.state()
        bad = self.ctx.call("trurl_hypotheses", action="maintenance-complete", plan_id=first["plan_id"],
                            review=first["hypothesis_maintenance"], outcome="no_proposal",
                            reason="This old review must not cover newly added evidence.")
        self.assertIn("error", bad)
        self.assertIn("error", self.ctx.call("trurl_plan", plan_id=first["plan_id"]))
        self.assertEqual(self.state(), before)

    def test_graph_completion_is_explicit_and_bound_to_exact_research(self):
        plan = self.plan()
        items = [candidate("tea-book", "book")]
        before = self.state()
        self.assertIn("Graph update is pending", self.ctx.call("trurl_select", plan_id=plan["plan_id"],
                                                               candidates=items, commit=True)["error"])
        self.assertEqual(self.state(), before)
        review = self.call("trurl_graph", action="plan", plan_id=plan["plan_id"], candidates=items)
        changed = deepcopy(items)
        changed[0]["description"] = "Different research now supports a different description."
        self.assertIn("error", self.ctx.call("trurl_graph", action="complete", plan_id=plan["plan_id"],
                                            candidates=changed, review=review, reason="Stale review."))
        self.graph_review(plan, items)
        self.assertIn("error", self.ctx.call("trurl_select", plan_id=plan["plan_id"], candidates=changed))
        result = self.call("trurl_select", plan_id=plan["plan_id"], candidates=items, commit=True)
        self.assertEqual(len(result["picks"]), 1)
        batch = self.state()["batches"][plan["plan_id"]]
        self.assertEqual(batch["graph_update"]["receipt"]["outcome"], "no_supported_changes")
        self.assertTrue(self.call("trurl_select", plan_id=plan["plan_id"], candidates=[], commit=True)["already_recorded"])
        self.assertEqual(len(self.state()["history"]), 1)

    def test_sourced_graph_routes_persist_in_sqlite_and_appear_in_atlas(self):
        plan, item = self.plan(), candidate("tea-cities", "book")
        before = self.state()["profile"]
        self.graph_review(plan, [item], self.content_delta(item))
        self.call("trurl_select", plan_id=plan["plan_id"], candidates=[item], commit=True)
        self.ctx = FakeContext(self.data)
        self.plugin.register(self.ctx)
        exported = self.call("trurl_atlas")
        data = json.loads(re.search(r'<script id="atlas-data" type="application/json">(.*?)</script>',
                                   Path(exported["output"]).read_text(), re.S).group(1))
        self.assertEqual(exported["connections"]["represented_interests"], 2)
        self.assertEqual(exported["connections"]["sourced_interests"], 2)
        self.assertTrue(data["connections"]["works"][0]["recommended"])
        self.assertEqual(self.state()["profile"], before)
        next_plan = finish_hypothesis_review(self.ctx, self.plan("broad"))
        self.assertIn("graph_frontier", next_plan["discovery"])
        self.assertEqual(len(self.call("trurl_graph", action="records", kind="edges")["items"]), 2)

    def test_partial_verified_edge_cannot_replace_its_provenance_with_another_work(self):
        first, item = self.plan(), candidate("tea-cities", "book")
        self.graph_review(first, [item], self.content_delta(item))
        second, other = self.plan(), candidate("another-work", "book")
        review = self.call("trurl_graph", action="plan", plan_id=second["plan_id"], candidates=[other])
        delta = {"schema_version": 1,
                 "sources": [{"id": "publisher-b", "kind": "publisher", "locator": other["canonical_url"],
                              "checked_at": "2026-11-09"}], "nodes": [],
                 "edges": [{**{key: value for key, value in self.state()["graph"]["edges"][0].items()
                                if key not in {"status", "support_note"}}, "source_provenance": ["publisher-b"]}]}
        for note in (None, "A fresh-looking note cannot verify a source for another work."):
            if note:
                delta["edges"][0]["support_note"] = note
            before = self.state()
            result = self.ctx.call("trurl_graph", action="complete", plan_id=second["plan_id"],
                                   candidates=[other], review=review, delta=delta, reason="Invalid synthetic update.")
            self.assertIn("error", result)
            self.assertEqual(self.state(), before)

    def test_damaged_hypothesis_receipt_recovers_and_can_be_completed(self):
        state = self.state()
        state["hypothesis_maintenance"] = {"schema_version": 1, "outcome": []}
        self.store(state)
        plan = self.plan("broad")
        self.assertIn("invalid_record", plan["hypothesis_maintenance"]["triggers"])
        resumed = finish_hypothesis_review(self.ctx, plan)
        self.assertIsNotNone(resumed["discovery"])
        self.assertEqual(self.state()["hypothesis_maintenance"]["outcome"], "no_proposal")

    def test_context_stays_compact_and_original_records_are_paged_selectively(self):
        before = self.call("trurl_context")
        state = self.state()
        for index in range(1000):
            state["history"].append({"id": "r" + str(index), "title": "RAW_HISTORY_TRACE" * 100})
            state["batches"]["b" + str(index)] = {"candidate_pool": "RAW_BATCH_TRACE" * 100}
        state["feedback"] = {"f" + str(i): {"id": "f" + str(i), "user_words": "ACTUAL_WORDS_" + str(i)}
                             for i in range(25)}
        self.store(state)
        compact = self.call("trurl_context")
        self.assertEqual(len(compact["interests"]), 2)
        self.assertFalse(compact["settings"]["delivery"]["weekly"]["enabled"])
        self.assertEqual(compact["settings"]["onboarding"]["context_sources"]["agent_memory"]["permission"], "declined")
        self.assertNotIn("RAW_HISTORY_TRACE", json.dumps(compact))
        self.assertNotIn("RAW_BATCH_TRACE", json.dumps(compact))
        self.assertNotIn("ACTUAL_WORDS", json.dumps(compact))
        self.assertLess(len(json.dumps(compact)) - len(json.dumps(before)), 30)
        page = self.call("trurl_context", action="records", kind="feedback", offset=10, limit=5)
        self.assertEqual(page["items"][0]["record"]["user_words"], "ACTUAL_WORDS_10")
        self.assertEqual(page["next_offset"], 15)
        self.assertIn("error", self.ctx.call("trurl_context", action="records", kind="feedback", limit=21))
        self.assertIn("error", self.ctx.call("trurl_context", action="prepare", ids=["r0"]))

    def test_all_explicit_interests_gain_representation_without_content_claims(self):
        profile = self.call("trurl_taste", interests=["Quiet theme " + str(i) for i in range(25)])["profile"]
        self.assertEqual(len(profile["interests"]), 27)
        state = self.state()
        self.assertEqual(len(state["graph_anchors"]["mappings"]), 27)
        self.assertEqual(len(state["graph"]["nodes"]), 27)
        self.assertEqual(state["graph"]["sources"], [])
        self.assertEqual(state["graph"]["edges"], [])
        self.assertEqual(self.call("trurl_atlas")["connections"]["represented_interests"], 27)
        self.assertEqual(self.state()["profile"], profile)

    def test_existing_schema_two_delivery_replays_without_new_history_or_preference_changes(self):
        plan, item = self.plan(), candidate("legacy-book", "book")
        self.graph_review(plan, [item])
        delivered = self.call("trurl_select", plan_id=plan["plan_id"], candidates=[item], commit=True)
        state = self.state()
        for key in ("graph", "graph_anchors", "graph_maintenance", "hypothesis_maintenance"):
            state.pop(key, None)
        for key in ("workflow_version", "planning_evidence", "discovery_seed", "graph_update", "hypothesis_maintenance"):
            state["batches"][plan["plan_id"]].pop(key, None)
        self.store(state)
        self.ctx = FakeContext(self.data)
        self.plugin.register(self.ctx)
        current = self.call("trurl_context")
        self.assertEqual(current["ledgers"]["recommendation"]["count"], 1)
        result = self.call("trurl_select", plan_id=plan["plan_id"], candidates=[], commit=True)
        self.assertEqual(result["picks"], delivered["picks"])
        self.assertTrue(result["already_recorded"])
        self.assertEqual(self.state(), state)

    def test_delivered_trace_preserves_exact_reviewed_pool_before_selection_normalization(self):
        plan = self.plan()
        book = candidate("reviewed-book", "book")
        book["title"] = "  Reviewed   book  "
        book["description"] = "  Synthetic   original description.  "
        film = candidate("filtered-film", "film")
        original = deepcopy([book, film])
        self.graph_review(plan, original)
        self.call("trurl_select", plan_id=plan["plan_id"], candidates=original, commit=True)
        batch = self.state()["batches"][plan["plan_id"]]
        self.assertEqual(batch["research_pool"], original)
        self.assertEqual(len(batch["candidate_pool"]), 1)
        self.assertEqual(batch["candidate_pool"][0]["title"], "Reviewed book")
        helper = importlib.import_module(self.plugin.__name__ + ".engine.scripts.graph_updates")
        self.assertEqual(helper._research(batch["research_pool"])["fingerprint"],
                         batch["graph_update"]["research_fingerprint"])
        self.assertNotEqual(helper._research(batch["candidate_pool"])["fingerprint"],
                            batch["graph_update"]["research_fingerprint"])
        self.assertTrue(self.call("trurl_select", plan_id=plan["plan_id"], candidates=[], commit=True)["already_recorded"])
        self.assertEqual(self.state()["batches"][plan["plan_id"]], batch)


if __name__ == "__main__":
    unittest.main()
