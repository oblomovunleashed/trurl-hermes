"""Exercise consent and actual native scheduling independently of Hermes writes."""

from copy import deepcopy
import importlib.util
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("trurl_scheduling_test", ROOT / "scheduling.py")
scheduling = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(scheduling)


def state(pending=True):
    return {"profile": {"person": {"id": "synthetic-reader"}, "interests": [],
                        "delivery": {"weekly": {"enabled": False, "onboarding_default_pending": pending,
                                                "day": "sunday", "time": "20:00", "timezone": "Europe/London",
                                                "time_confirmed": False}}}}


def job(**changes):
    value = {"job_id": "trurl-sunday-test", "name": "Trurl Sunday edition", "skills": ["trurl:trurl"],
             "skill": "trurl:trurl", "schedule": "0 20 * * 0", "repeat": "forever", "enabled": True,
             "state": "scheduled", "paused_at": None, "next_run_at": "2026-11-08T20:00:00+00:00", "deliver": "local"}
    value.update(changes)
    return value


class NativeList:
    def __init__(self, jobs=(), gateway=True):
        self.response = {"success": True, "jobs": list(jobs), "gateway_running": gateway}
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return deepcopy(self.response)


class SchedulingTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data_dir = Path(self.temp.name) / "uncreated-plugin-data"
        self.state = state()
        self.native = NativeList()

    def call(self, action, **args):
        return scheduling.schedule_tool({"action": action, **args}, self.state, self.data_dir, self.native)

    def explicit_args(self, **changes):
        args = {"action": "confirm", "authorization": "explicit_request", "user_words": "Send my Sunday edition at 8 pm London time",
                "job_id": "trurl-sunday-test", "timezone": "Europe/London", "time": "20:00"}
        args.update(changes)
        return args

    def confirm(self, **changes):
        return scheduling.schedule_tool(self.explicit_args(**changes), self.state, self.data_dir, self.native)

    def test_notice_discloses_slot_and_optout_without_activating_or_writing_files(self):
        result, changed = self.call("notice")
        self.assertTrue(changed)
        self.assertTrue(result["notice_issued"])
        self.assertIn("No Sundays", result["message"])
        self.assertEqual(result["native_schedule"]["schedule"], "0 20 * * 0")
        self.assertFalse(result["weekly_enabled"])
        self.assertFalse(result["delivery_active"])
        self.assertFalse(result["native_job_writes_performed"])
        self.assertFalse(self.data_dir.exists())
        with self.assertRaisesRegex(ValueError, "disabled"):
            scheduling.check_schedule(self.state["profile"], "weekly", self.native)

    def test_onboarding_needs_prior_notice_actual_continuation_and_native_job(self):
        with self.assertRaisesRegex(ValueError, "earlier notice"):
            self.confirm(authorization="onboarding_continued", notice_id="made-up")
        notice, _ = self.call("notice")
        self.native.response["jobs"] = [job()]
        for args in ({"authorization": "onboarding_continued", "notice_id": notice["notice_id"], "user_words": ""},
                     {"authorization": "onboarding_continued", "notice_id": "wrong"}):
            with self.subTest(args=args), self.assertRaises(ValueError):
                self.confirm(**args)
        result, changed = self.confirm(authorization="onboarding_continued", notice_id=notice["notice_id"],
                                       user_words="Books and films please, with something funny")
        self.assertTrue(changed)
        self.assertTrue(result["confirmed"])
        self.assertFalse(result["onboarding_default_pending"])
        weekly = self.state["profile"]["delivery"]["weekly"]
        self.assertEqual(weekly["automation_id"], "trurl-sunday-test")
        self.assertEqual(weekly["last_schedule_authorization"]["kind"], "onboarding_continued")
        self.assertFalse(weekly["time_confirmed"])
        scheduling.check_schedule(self.state["profile"], "weekly", self.native)

    def test_confirmation_rejects_changed_disclosed_slot(self):
        notice, _ = self.call("notice")
        before = deepcopy(self.state)
        self.native.response["jobs"] = [job(schedule="0 19 * * 0", next_run_at="2026-11-08T19:00:00+00:00")]
        with self.assertRaisesRegex(ValueError, "match the notice"):
            self.confirm(authorization="onboarding_continued", notice_id=notice["notice_id"], time="19:00")
        self.assertEqual(self.state, before)

    def test_existing_profile_cannot_be_default_enrolled(self):
        self.state = state(pending=False)
        before = deepcopy(self.state)
        result, changed = self.call("notice")
        self.assertFalse(changed)
        self.assertFalse(result["notice_issued"])
        self.native.response["jobs"] = [job()]
        with self.assertRaisesRegex(ValueError, "pending enrollment"):
            self.confirm(authorization="onboarding_continued", notice_id="irrelevant")
        self.assertEqual(before, self.state)
        result, _ = self.confirm()
        self.assertTrue(result["confirmed"])

    def test_off_gate_is_immediate_but_native_pause_is_not_fabricated(self):
        self.native.response["jobs"] = [job(deliver="telegram")]
        self.confirm()
        result, changed = self.call("off", user_words="Turn off Sunday delivery")
        self.assertTrue(changed)
        self.assertFalse(result["weekly_enabled"])
        self.assertFalse(result["delivery_active"])
        self.assertTrue(result["native"]["active"])
        self.assertEqual(result["pause_job_ids"], ["trurl-sunday-test"])
        self.assertFalse(result["native_pause_confirmed"])
        self.assertTrue(result["native_pause_check_required"])
        before_calls = self.native.calls
        with self.assertRaisesRegex(ValueError, "disabled"):
            scheduling.check_schedule(self.state["profile"], "weekly", self.native)
        self.assertEqual(self.native.calls, before_calls)

    def test_decline_clears_pending_and_prevents_later_onboarding_activation(self):
        notice, _ = self.call("notice")
        result, _ = self.call("decline", user_words="No Sundays")
        self.assertFalse(result["onboarding_default_pending"])
        self.native.response["jobs"] = [job()]
        with self.assertRaises(ValueError):
            self.confirm(authorization="onboarding_continued", notice_id=notice["notice_id"])

    def test_native_absence_and_gateway_failure_never_enable_delivery(self):
        responses = [None, {"success": False, "jobs": [job()]}, {"success": True, "jobs": "bad"},
                     {"success": True, "jobs": []},
                     {"success": True, "jobs": [job()], "gateway_running": False},
                     {"success": True, "jobs": [job()], "gateway_running": None}]
        for response in responses:
            with self.subTest(response=response):
                current = state()
                result, _ = scheduling.schedule_tool(self.explicit_args(), current, self.data_dir, lambda: response)
                self.assertFalse(result["confirmed"])
                self.assertFalse(current["profile"]["delivery"]["weekly"]["enabled"])
                self.assertFalse(current["profile"]["delivery"]["weekly"]["onboarding_default_pending"])
        def broken():
            raise RuntimeError("do not expose a provider error")
        result, _ = scheduling.schedule_tool(self.explicit_args(), state(), self.data_dir, broken)
        self.assertEqual(result["message"], "Native scheduler inspection is unavailable.")

    def test_invalid_native_jobs_are_not_adopted(self):
        variants = [job(enabled=False), job(state="paused"), job(paused_at="2026-11-06T10:00:00Z"),
                    job(state="completed"), job(repeat="once"), job(repeat="5 times"),
                    job(name="Unrelated weekly report", skills=[], skill=None),
                    job(schedule="0 20 * * 1"), job(schedule="0 20 * * *"), job(schedule="30 20 * * 0"),
                    job(schedule="0 20 8 * 0"), job(timezone="America/New_York"),
                    job(next_run_at="2026-11-08T20:00:00"), job(next_run_at="2026-11-08T20:00:00-05:00"),
                    job(next_run_at=None)]
        for native_job in variants:
            with self.subTest(job=native_job):
                current = state()
                result, _ = scheduling.schedule_tool(self.explicit_args(), current, self.data_dir, NativeList([native_job]))
                self.assertFalse(result["confirmed"])
                self.assertNotIn("automation_id", current["profile"]["delivery"]["weekly"])

    def test_weekly_guard_rechecks_native_state_and_status_is_read_only(self):
        self.native.response["jobs"] = [job()]
        self.confirm()
        before = deepcopy(self.state)
        self.native.response["jobs"][0].update(enabled=False, state="paused")
        with self.assertRaisesRegex(ValueError, "paused"):
            scheduling.check_schedule(self.state["profile"], "weekly", self.native)
        result, changed = self.call("status")
        self.assertFalse(changed)
        self.assertTrue(result["weekly_enabled"])
        self.assertFalse(result["delivery_active"])
        self.assertEqual(self.state, before)

    def test_legacy_enabled_without_native_job_cannot_run(self):
        self.state["profile"]["delivery"]["weekly"]["enabled"] = True
        self.native.response["jobs"] = [job()]
        with self.assertRaisesRegex(ValueError, "No unique"):
            scheduling.check_schedule(self.state["profile"], "weekly", self.native)

    def test_notice_detects_existing_paused_job_without_resuming_or_duplicate_setup(self):
        self.native.response["jobs"] = [job(enabled=False, state="paused")]
        result, changed = self.call("notice")
        self.assertTrue(changed)
        self.assertFalse(result["notice_issued"])
        self.assertFalse(result["onboarding_default_pending"])
        self.assertEqual(result["existing_job_ids"], ["trurl-sunday-test"])
        self.assertFalse(self.native.response["jobs"][0]["enabled"])

    def test_missing_timezone_waits_without_issuing_notice(self):
        self.state["profile"]["delivery"]["weekly"]["timezone"] = None
        result, changed = self.call("notice")
        self.assertFalse(changed)
        self.assertFalse(result["notice_issued"])
        self.assertTrue(result["requires_timezone"])
        self.assertTrue(result["onboarding_default_pending"])

    def test_invalid_time_and_timezone_are_rejected_without_mutation(self):
        for overrides in ({"time": "25:00"}, {"time": "8pm"}, {"timezone": "unknown/location"}, {"timezone": ""}):
            with self.subTest(overrides=overrides):
                before = deepcopy(self.state)
                with self.assertRaises(ValueError):
                    self.call("notice", **overrides)
                self.assertEqual(before, self.state)

    def test_exact_local_time_is_verified_across_utc_offsets(self):
        self.native.response["jobs"] = [job(schedule="30 17 * * SUN", timezone="Asia/Tokyo", next_run_at="2026-11-08T08:30:00Z")]
        result, _ = self.confirm(time="17:30", timezone="Asia/Tokyo")
        self.assertTrue(result["confirmed"])
        self.assertTrue(result["native"]["timezone_verified"])
        self.assertEqual(result["native"]["next_run_local"], "2026-11-08T17:30:00+09:00")

    def test_formatter_without_timezone_pin_has_explicit_limit_and_no_delivery_claim(self):
        self.native.response["jobs"] = [job(schedule="every sunday 8pm")]
        result, _ = self.confirm()
        self.assertTrue(result["confirmed"])
        self.assertFalse(result["native"]["timezone_verified"])
        self.assertIn("daylight-saving", result["native"]["timezone_limit"])
        self.assertFalse(result["native"]["delivery_verified"])
        self.assertIn("message delivery is not configured", result["native"]["delivery_note"])

    def test_profile_notices_and_callback_job_inventory_do_not_cross_profiles(self):
        notice, _ = self.call("notice")
        other = state()
        with self.assertRaises(ValueError):
            scheduling.schedule_tool(self.explicit_args(authorization="onboarding_continued", notice_id=notice["notice_id"]),
                                     other, self.data_dir, NativeList([job()]))
        self.native.response["jobs"] = [job()]
        self.confirm()
        with self.assertRaisesRegex(ValueError, "No unique"):
            scheduling.check_schedule(self.state["profile"], "weekly", NativeList())
        self.assertFalse(other["profile"]["delivery"]["weekly"]["enabled"])

    def test_duplicate_native_job_ids_fail_closed(self):
        self.native.response["jobs"] = [job(), job()]
        result, _ = self.confirm()
        self.assertFalse(result["confirmed"])

    def test_on_demand_never_needs_or_calls_scheduler(self):
        def forbidden():
            self.fail("On-demand curation must not inspect the scheduler")
        scheduling.check_schedule(self.state["profile"], "on_demand", forbidden)


if __name__ == "__main__":
    unittest.main()
