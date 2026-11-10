"""Keep personal-fit and open-value scoring distinct in durable native history."""

from contextlib import closing
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
from tempfile import TemporaryDirectory
import unittest

from test_workflow import FakeContext, candidate, finish_graph_review, finish_hypothesis_review


ROOT = Path(__file__).resolve().parents[1]


class RecordingMetadataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location(
            "trurl_recording_metadata", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
        cls.plugin = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = cls.plugin
        spec.loader.exec_module(cls.plugin)

    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.data = Path(temporary.name) / "profile"
        self.ctx = FakeContext(self.data)
        self.plugin.register(self.ctx)
        self.call("trurl_taste", interests=["Rivers"], categories=["book"])

    def call(self, name, **args):
        result = self.ctx.call(name, **args)
        self.assertNotIn("error", result, result)
        return result

    def state(self):
        with closing(sqlite3.connect(self.data / "trurl.sqlite3")) as db:
            return json.loads(db.execute("SELECT data FROM curation WHERE id=1").fetchone()[0])

    def deliver(self, item, mode, scope):
        plan = finish_hypothesis_review(self.ctx, self.call(
            "trurl_plan", request="One verified book", categories=["book"], count=1, mode=mode, scope=scope))
        finish_graph_review(self.ctx, plan["plan_id"], [item])
        result = self.call("trurl_select", plan_id=plan["plan_id"], candidates=[item], commit=True)
        self.assertEqual(len(result["picks"]), 1)
        before = self.state()
        self.ctx = FakeContext(self.data)
        self.plugin.register(self.ctx)
        retry = self.call("trurl_select", plan_id=plan["plan_id"], candidates=[], commit=True)
        self.assertTrue(retry["already_recorded"])
        self.assertEqual(self.state(), before)
        return before["history"][0], before["batches"][plan["plan_id"]]["selection_plan"]["selected"][0]

    def test_personal_delivery_retains_six_axis_score_metadata(self):
        item = candidate("river-history", "book")
        item.update(personal_fit_status="evidenced_anchor", source_family="synthetic-publisher",
                    discovery_domain="history")
        saved, selected = self.deliver(item, "personal", "focused")
        self.assertEqual(saved["scoring_basis"], "six_axis_weighted_fit")
        self.assertEqual(saved["weighted_total"], selected["base_score"])
        for field in ("scores", "scoring_weights", "personal_fit_status", "source_family", "discovery_domain"):
            self.assertEqual(saved[field], selected[field])

    def test_open_delivery_preserves_unknown_fit_without_a_personal_weighted_total(self):
        item = candidate("unrelated-craft", "book")
        item.update(lane="distant", personal_fit_status="unknown",
                    value_rationale="A clear account of a craft that opens an unfamiliar field.")
        item["scores"]["relevance"] = None
        saved, selected = self.deliver(item, "open", "broad")
        self.assertEqual(selected["scoring_basis"], "open_value_without_personal_relevance")
        self.assertIsNone(saved["weighted_total"])
        self.assertIsNone(saved["scores"]["relevance"])
        for field in ("scoring_basis", "scoring_weights", "personal_fit_status", "value_rationale"):
            self.assertEqual(saved[field], selected[field])
        self.assertNotIn("relevance", saved["scoring_weights"])

    def test_upgrade_repairs_open_history_from_the_saved_batch_evidence(self):
        item = candidate("unrelated-craft", "book")
        item.update(lane="distant", personal_fit_status="unknown",
                    value_rationale="An accessible account of a craft outside the person's stated interests.")
        item["scores"]["relevance"] = None
        self.deliver(item, "open", "broad")
        legacy = self.state()
        legacy["schema_version"] = 2
        legacy["history"][0]["weighted_total"] = 4.0
        for field in ("scoring_basis", "scoring_weights", "personal_fit_status", "value_rationale"):
            legacy["history"][0].pop(field)
        original_id = legacy["history"][0]["id"]
        with closing(sqlite3.connect(self.data / "trurl.sqlite3")) as db, db:
            db.execute("UPDATE curation SET data=? WHERE id=1", (json.dumps(legacy),))
        self.call("trurl_context", action="prepare")
        restored = self.state()["history"][0]
        self.assertEqual(restored["id"], original_id)
        self.assertIsNone(restored["weighted_total"])
        self.assertEqual(restored["scoring_basis"], "open_value_without_personal_relevance")
        self.assertEqual(restored["personal_fit_status"], "unknown")
        self.assertEqual(restored["value_rationale"], item["value_rationale"])


if __name__ == "__main__":
    unittest.main()
