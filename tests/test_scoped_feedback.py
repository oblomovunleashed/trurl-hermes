"""General corrections retain their scope without inventing a delivered work."""

import importlib.util
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

from test_workflow import FakeContext


class ScopedFeedbackTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1]
        spec = importlib.util.spec_from_file_location("trurl_scoped_feedback_test", root / "__init__.py",
                                                     submodule_search_locations=[str(root)])
        cls.plugin = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = cls.plugin
        spec.loader.exec_module(cls.plugin)

    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.ctx = FakeContext(Path(temp.name))
        self.plugin.register(self.ctx)

    def call(self, name, **args):
        result = self.ctx.call(name, **args)
        self.assertNotIn("error", result, result)
        return result

    def test_general_feedback_needs_words_target_and_scope_then_survives_reopen(self):
        self.call("trurl_taste", interests=["Tea"])
        profile = self.call("trurl_taste")["profile"]
        args = dict(reaction="too_introductory", scope="topic", target="Tea",
                    note="The tea suggestions are too introductory for me.")
        self.call("trurl_feedback", **args)
        self.assertTrue(self.call("trurl_feedback", **args)["already_recorded"])
        reopened = FakeContext(self.ctx.state.data_dir)
        self.plugin.register(reopened)
        self.ctx = reopened
        snapshot = self.call("trurl_brief", action="snapshot")
        record = self.call("trurl_context", action="records", kind="feedback")["items"][0]["record"]
        self.assertEqual((record["scope"], record["target"], record["user_words"]),
                         ("topic", "Tea", args["note"]))
        self.assertIsNone(record["recommendation_id"])
        self.assertEqual(self.call("trurl_taste")["profile"], profile)
        self.assertEqual(snapshot["context"]["ledgers"]["recommendation"]["count"], 0)
        for missing in ("scope", "target", "note"):
            invalid = {key: value for key, value in args.items() if key != missing}
            self.assertIn("error", self.ctx.call("trurl_feedback", **invalid))

    def test_request_correction_stays_scoped_and_invalidates_pending_plan(self):
        self.call("trurl_taste", interests=["Tea"])
        profile = self.call("trurl_taste")["profile"]
        plan = self.call("trurl_plan", request="One book", count=1)
        self.call("trurl_feedback", reaction="correction", scope="current_request", target=plan["plan_id"],
                  note="Less technical this time.")
        self.assertEqual(self.call("trurl_taste")["profile"], profile)
        self.assertIn("error", self.ctx.call("trurl_select", plan_id=plan["plan_id"], candidates=[]))
        self.assertIn("error", self.ctx.call("trurl_feedback", reaction="more_like_this", scope="current_request",
                                            target=plan["plan_id"], note="This time only.", interest="History"))

    def test_explicit_preference_updates_reach_next_plan_and_imports_cannot_change_them(self):
        self.call("trurl_taste", preferences={"constraints": ["No horror"], "presentation": "One short paragraph"},
                  user_words="Always avoid horror and keep the answer to one short paragraph.")
        plan = self.call("trurl_plan", request="A film", categories=["film"], count=1)
        self.assertEqual(plan["lasting_constraints"], ["No horror"])
        self.assertEqual(plan["presentation"]["default"], "One short paragraph")
        self.assertIn("error", self.ctx.call("trurl_taste", source="agent_memory", preferences={"constraints": []},
                                            user_words="Clear exclusions"))


if __name__ == "__main__":
    unittest.main()
