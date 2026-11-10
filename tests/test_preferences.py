"""Exercise explicit durable preferences against the actual request planner."""

from copy import deepcopy
import importlib.util
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]


class PreferencesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        package = "trurl_preferences_under_test"
        spec = importlib.util.spec_from_file_location(package, ROOT / "__init__.py",
                                                      submodule_search_locations=[str(ROOT)])
        module = importlib.util.module_from_spec(spec)
        sys.modules[package] = module
        cls.preferences = importlib.import_module(package + ".preferences")
        cls.curation = importlib.import_module(package + ".engine.scripts.curation")

    def setUp(self):
        self.state = {"profile": self.curation.generic_profile(), "history": [], "feedback": {}}

    def apply(self, values, words="Use these as my lasting preferences."):
        return self.preferences.apply_preferences({"preferences": values, "user_words": words}, self.state)

    def plan(self, **kwargs):
        args = dict(request="A broad selection", mode="personal", scope="broad", seed=42)
        args.update(kwargs)
        return self.curation.make_plan(self.state["profile"], **args)

    def test_explicit_baseline_is_normalized_audited_and_used_by_planner(self):
        weights = dict.fromkeys(self.curation.AXES, 1)
        weights["depth"] = 5
        before = deepcopy(self.state["profile"]["taste"]["axes_weights"])
        profile_ref = self.state["profile"]
        changes = self.apply({"axes_weights": weights}, "Give depth five times the priority of every other axis.")
        expected = {axis: weight / 10 for axis, weight in weights.items()}
        self.assertIs(self.state["profile"], profile_ref)
        self.assertEqual(self.plan()["effective_weights"], expected)
        self.assertEqual(self.state["profile"]["taste"]["weights_status"], "explicit_user_preference")
        adjustment = next(item for item in changes if item["path"] == "/taste/axes_weights")
        self.assertEqual(adjustment["before"], before)
        self.assertEqual(adjustment["after"], expected)
        event = next(iter(self.state["feedback"].values()))
        self.assertEqual(event["profile_changes"], changes)
        self.assertEqual(event["scope"], "global")
        self.assertIsNone(event["recommendation_id"])
        saved = deepcopy(self.state)
        surprise = self.plan(mode="surprise")
        self.assertNotEqual(surprise["effective_weights"], expected)
        self.assertEqual(self.state, saved)

    def test_fixed_default_stops_rotation_and_keeps_explicit_request_override(self):
        self.apply({"variation": "fixed"})
        plan = self.plan(mode="auto", repeat_count=10)
        self.assertEqual(plan["trigger"], "profile_fixed_policy")
        self.assertFalse(plan["variation_applied"])
        self.assertEqual(plan["effective_weights"], plan["baseline_weights"])
        self.assertEqual(self.plan(mode="surprise")["mode"], "surprise")
        self.apply({"variation": "adaptive"})
        self.assertTrue(self.plan(mode="auto", repeat_count=10)["variation_applied"])

    def test_standing_open_choice_is_scoped_and_current_optout_is_temporary(self):
        self.apply({"open_exploration": True, "open_exploration_scope": "Broad leisure selections only"})
        profile = deepcopy(self.state["profile"])
        plan = self.plan()
        self.assertEqual(plan["lane_mix"], {"familiar": .5, "adjacent": .25, "distant": .25})
        self.assertEqual(plan["policy_version"], "open_exploration_v1")
        self.assertTrue(plan["constraints"]["open_exploration_enabled"])
        self.assertEqual(profile["discovery"]["open_exploration"]["scope"], "Broad leisure selections only")
        close = self.plan(request="Stay close to my interests this time")
        self.assertEqual(close["lane_mix"]["distant"], 0)
        self.assertFalse(close["constraints"]["open_exploration_enabled"])
        self.assertEqual(self.state["profile"], profile)
        self.apply({"open_exploration": False, "open_exploration_scope": "All future selections"})
        self.assertEqual(self.plan()["lane_mix"], {"familiar": .75, "adjacent": .25, "distant": 0})
        self.assertFalse(self.state["profile"]["discovery"]["open_exploration"]["enabled"])

    def test_constraints_presentation_and_hops_preserve_scope_and_before_values(self):
        first = ["No horror", "Prefer material under 40 minutes"]
        self.apply({"constraints": first, "presentation": "A linked list with one sentence per work", "graph_max_hops": 3},
                   "Always avoid horror and use these limits and presentation.")
        profile = self.state["profile"]
        self.assertEqual(profile["taste"]["constraints"], first)
        self.assertEqual(profile["taste"]["presentation"]["kind"], "explicit")
        self.assertEqual(self.plan()["constraints"]["graph_max_hops"], 3)
        changes = self.apply({"constraints": ["No horror"]}, "Remove the time limit, but keep avoiding horror.")
        self.assertEqual(changes[0]["before"], first)
        self.assertEqual(changes[0]["after"], ["No horror"])
        self.assertEqual(changes[0]["scope"], "persistent")
        origin = changes[0]["source_provenance"][0]
        source = next(record for record in profile["provenance"] if record["id"] == origin)
        self.assertEqual(source["source"], "current_conversation")
        self.assertEqual(source["user_words"], "Remove the time limit, but keep avoiding horror.")
        self.apply({"constraints": []}, "Clear all my lasting content constraints.")
        self.assertEqual(profile["taste"]["constraints"], [])

    def test_identical_preferences_are_idempotent_and_audit_keys_are_deterministic(self):
        original = deepcopy(self.state)
        values = {"constraints": ["No horror"], "presentation": "One plain sentence per work",
                  "axes_weights": dict.fromkeys(self.curation.AXES, 1), "variation": "fixed",
                  "open_exploration": True, "open_exploration_scope": "Broad leisure", "graph_max_hops": 3}
        self.apply(values)
        after = deepcopy(self.state)
        self.assertEqual(self.apply(values), [])
        self.assertEqual(self.state, after)
        other = deepcopy(original)
        self.preferences.apply_preferences({"preferences": values, "user_words": "Use these as my lasting preferences."}, other)
        self.assertEqual(list(other["feedback"]), list(self.state["feedback"]))
        self.assertEqual(other["feedback"][next(iter(other["feedback"]))]["profile_changes"],
                         after["feedback"][next(iter(after["feedback"]))]["profile_changes"])

    def test_invalid_input_never_partially_changes_preferences_or_audit(self):
        invalid_values = [
            {}, [], {"unknown": True}, {"constraints": "No horror"}, {"constraints": ["x"] * 31},
            {"constraints": [" "]}, {"constraints": [False]}, {"presentation": ""},
            {"axes_weights": {"depth": 1}}, {"axes_weights": dict.fromkeys(self.curation.AXES, 0)},
            {"axes_weights": dict.fromkeys(self.curation.AXES, float("nan"))},
            {"axes_weights": dict.fromkeys(self.curation.AXES, 1e308)},
            {"variation": []}, {"variation": "sometimes"}, {"open_exploration": 1},
            {"open_exploration": True}, {"open_exploration": False, "open_exploration_scope": " "},
            {"open_exploration_scope": "Leisure"}, {"graph_max_hops": True}, {"graph_max_hops": 4},
            {"constraints": ["A valid change"], "presentation": "A valid display", "graph_max_hops": 0},
        ]
        for values in invalid_values:
            with self.subTest(values=values):
                before = deepcopy(self.state)
                with self.assertRaises(ValueError):
                    self.apply(values)
                self.assertEqual(self.state, before)
        for words in (None, "", " ", 42, "x" * 4001):
            with self.subTest(words=words):
                before = deepcopy(self.state)
                with self.assertRaises(ValueError):
                    self.apply({"constraints": ["No horror"]}, words)
                self.assertEqual(self.state, before)


if __name__ == "__main__":
    unittest.main()
