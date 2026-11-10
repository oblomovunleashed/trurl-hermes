"""Exercise the plugin tools with isolated storage and synthetic candidates."""

import importlib.util
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from xml.etree import ElementTree


ROOT = Path(__file__).resolve().parents[1]
AXES = ("depth", "novelty", "relevance", "surprise", "perspective_change", "entertainment")


class FakeContext:
    def __init__(self, data_dir):
        self.state = SimpleNamespace(data_dir=data_dir)
        self.tools = {}
        self.skills = []
        self.jobs = []

    def register_tool(self, name, toolset, schema, handler, **kwargs):
        self.tools[name] = handler

    def register_skill(self, name, path, **kwargs):
        self.skills.append((name, Path(path)))

    def call(self, name, **args):
        return json.loads(self.tools[name](args))

    def dispatch_tool(self, name, args):
        assert name == "cronjob_manage" and args == {"action": "list", "include_disabled": True}
        return {"success": True, "gateway_running": True, "jobs": self.jobs}


def candidate(slug, medium, *, credible=True, score=4):
    url = f"https://example.org/{slug}"
    return {
        "id": slug,
        "title": slug.replace("-", " ").title(),
        "creator": "Test Creator",
        "canonical_url": url,
        "medium": medium,
        "lane": "familiar",
        "topics": ["rivers"],
        "scores": dict.fromkeys(AXES, score),
        "credibility_passed": credible,
        "constraints_passed": True,
        "verification": {"checked_at": "2026-11-05", "urls": [url]},
        "description": "Rivers & tides.",
    }


def finish_hypothesis_review(ctx, plan):
    """The synthetic host explicitly reviews a due pool, then resumes its plan."""
    review = plan.get("hypothesis_maintenance", {})
    if review.get("review_due"):
        ids = review.get("pool", {}).get("testing_ids", [])
        result = ctx.call("trurl_hypotheses", action="maintenance-complete", plan_id=plan["plan_id"],
                          review=review, outcome="reviewed" if ids else "no_proposal",
                          hypothesis_ids=ids,
                          reason="Reviewed the synthetic original anchors; no additional preference is justified.")
        assert "error" not in result, result
        plan = ctx.call("trurl_plan", plan_id=plan["plan_id"])
        assert "error" not in plan, plan
    return plan


def finish_graph_review(ctx, plan_id, candidates):
    """Fixture candidates contain metadata only, with no reviewed content edges."""
    review = ctx.call("trurl_graph", action="plan", plan_id=plan_id, candidates=candidates)
    assert "error" not in review, review
    if review["review_due"]:
        result = ctx.call("trurl_graph", action="complete", plan_id=plan_id,
                          candidates=candidates, review=review,
                          reason="Reviewed the synthetic metadata; it supplies no supported content relationships.")
        assert "error" not in result, result


class WorkflowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location(
            "trurl_plugin_under_test", ROOT / "__init__.py",
            submodule_search_locations=[str(ROOT)],
        )
        cls.plugin = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = cls.plugin
        spec.loader.exec_module(cls.plugin)

    def setUp(self):
        workspace = TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.root = Path(workspace.name)
        self.ctx = self.open_profile("first")

    def open_profile(self, name):
        ctx = FakeContext(self.root / name)
        self.plugin.register(ctx)
        return ctx

    def checked(self, name, **args):
        result = self.ctx.call(name, **args)
        if name == "trurl_select" and result.get("error", "").startswith("Graph update is pending"):
            finish_graph_review(self.ctx, args["plan_id"], args["candidates"])
            result = self.ctx.call(name, **args)
        self.assertNotIn("error", result, result)
        return result

    def plan(self, **overrides):
        args = {
            "request": "Find a book and a podcast about rivers",
            "categories": ["book", "podcast"],
            "mode": "personal",
            "scope": "focused",
        }
        args.update(overrides)
        return self.checked("trurl_plan", **args)["plan_id"]

    def test_registration_leaves_profile_directory_unwritten(self):
        self.assertEqual(
            set(self.ctx.tools),
            {"trurl_taste", "trurl_plan", "trurl_select", "trurl_feedback", "trurl_brief",
             "trurl_hypotheses", "trurl_atlas", "trurl_schedule", "trurl_context", "trurl_graph"},
        )
        self.assertTrue(self.ctx.skills)
        self.assertTrue(all(path.is_file() for _, path in self.ctx.skills))
        self.assertFalse(self.ctx.state.data_dir.exists())

    def test_weekly_native_schedule_persists_per_profile_and_opt_out_stops_commit(self):
        request = {
            "request": "Make my Sunday edition",
            "edition_date": "2026-11-08",
            "context": "weekly",
            "categories": ["book"],
            "mode": "personal",
        }
        self.assertIn("error", self.ctx.call("trurl_plan", **request))
        self.checked("trurl_taste", interests=["Rivers"])
        self.assertIn("error", self.ctx.call("trurl_taste", weekly_enabled=True))
        jobs = [{"job_id": "test-sunday", "name": "Trurl Sunday edition", "skills": ["trurl:trurl"],
                 "schedule": "0 20 * * 0", "repeat": "forever", "enabled": True, "state": "scheduled",
                 "paused_at": None, "next_run_at": "2026-11-08T20:00:00+00:00", "deliver": "local"}]
        self.ctx.jobs = jobs
        confirmed = self.checked("trurl_schedule", action="confirm", job_id="test-sunday", timezone="Europe/London",
                                 authorization="explicit_request", user_words="Send my Sunday edition at 8pm London time")
        self.assertTrue(confirmed["confirmed"])
        self.assertIn("plan_id", self.checked("trurl_plan", **request))
        self.ctx = self.open_profile("first")
        self.ctx.jobs = jobs
        pending = self.checked("trurl_plan", **request)["plan_id"]
        other = self.open_profile("second")
        self.assertIn("error", other.call("trurl_plan", **request))
        self.checked("trurl_taste", weekly_enabled=False)
        self.assertIn("error", self.ctx.call(
            "trurl_select", plan_id=pending, commit=True,
            candidates=[candidate("river-book", "book")],
        ))

    def test_commit_preserves_category_shortfall_and_deduplicates_history(self):
        self.checked("trurl_taste", interests=["Rivers"])
        first_plan = self.plan(edition_date="2026-11-08")
        candidates = [
            candidate("river-book", "book"),
            candidate("weaker-book", "book", score=3),
            candidate("unverified-episode", "podcast", credible=False),
        ]
        preview = self.checked("trurl_select", plan_id=first_plan, candidates=candidates)
        self.assertEqual([pick["title"] for pick in preview["picks"]], ["River Book"])
        self.assertEqual(preview["missing_categories"], ["podcast"])
        self.assertEqual(preview["shortfall"], 1)

        committed = self.checked("trurl_select", plan_id=first_plan, candidates=candidates, commit=True)
        self.assertEqual([pick["title"] for pick in committed["picks"]], ["River Book"])
        retry = self.checked("trurl_select", plan_id=first_plan, candidates=candidates, commit=True)
        self.assertEqual(committed["picks"], retry["picks"])
        self.ctx = self.open_profile("first")
        second_plan = self.plan(edition_date="2026-11-15")
        repeated = self.checked(
            "trurl_select", plan_id=second_plan,
            candidates=[candidate("river-book", "book")],
        )
        self.assertEqual(repeated["picks"], [])
        self.assertEqual(repeated["missing_categories"], ["book", "podcast"])

    def test_feedback_records_item_reaction_and_explicit_interest_separately(self):
        self.checked("trurl_taste", interests=["Rivers"])
        selected = self.checked(
            "trurl_select", plan_id=self.plan(count=1), commit=True,
            candidates=[candidate("river-book", "book")],
        )
        pick_id = selected["picks"][0]["recommendation_id"]
        self.checked("trurl_feedback", recommendation_id=pick_id, reaction="liked")
        self.assertEqual(self.checked("trurl_taste")["interests"], ["Rivers"])
        self.assertIn("error", self.ctx.call(
            "trurl_feedback", recommendation_id=pick_id, reaction="liked", interest="Tides",
        ))
        args = {"recommendation_id": pick_id, "reaction": "more_like_this", "interest": "Tides"}
        self.checked("trurl_feedback", **args)
        self.checked("trurl_feedback", **args)
        self.ctx = self.open_profile("first")
        taste = self.checked("trurl_taste")
        self.assertEqual(taste["interests"], ["Rivers", "Tides"])
        self.assertEqual(taste["feedback_count"], 2)

    def test_dated_commit_writes_edition_inside_profile(self):
        self.checked("trurl_taste", interests=["Rivers"])
        plan_id = self.plan(edition_date="2026-11-08")
        selected = self.checked(
            "trurl_select", plan_id=plan_id, commit=True,
            candidates=[candidate("river-book", "book")],
        )
        files = selected["edition_files"]
        svg, markdown = (Path(files[key]) for key in ("svg", "markdown"))
        for path in (svg, markdown):
            self.assertTrue(path.resolve().is_relative_to(self.ctx.state.data_dir.resolve()))
        self.assertTrue(ElementTree.parse(svg).getroot().tag.endswith("}svg"))
        self.assertIn("https://example.org/river-book", markdown.read_text(encoding="utf-8"))
        self.assertIn("No verified pick this week.", markdown.read_text(encoding="utf-8"))

    def test_dated_commit_excludes_insecure_work_urls_without_losing_valid_picks(self):
        self.checked("trurl_taste", interests=["Rivers"])
        plan_id = self.plan(edition_date="2026-11-08")
        episode = candidate("river-episode", "podcast", score=5)
        episode["canonical_url"] = "http://example.org/river-episode"
        episode["verification"]["urls"] = [episode["canonical_url"]]
        selected = self.checked(
            "trurl_select", plan_id=plan_id, commit=True,
            candidates=[candidate("river-book", "book"), episode],
        )
        self.assertEqual([pick["title"] for pick in selected["picks"]], ["River Book"])
        self.assertEqual(selected["missing_categories"], ["podcast"])
        markdown = Path(selected["edition_files"]["markdown"]).read_text(encoding="utf-8")
        self.assertNotIn("river-episode", markdown)


if __name__ == "__main__":
    unittest.main()
