"""Confirm Hermes-owned Sunday schedules without creating or changing native jobs.

The caller supplies a profile-scoped, read-only ``cronjob_manage`` list callback.
Stored consent and an active native schedule are checked separately. This module
does not access Hermes files, start a process, or call a scheduler write action.
"""

from copy import deepcopy
from datetime import datetime, timezone
import re
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


ACTIONS = ("notice", "status", "confirm", "off", "decline")
AUTHORIZATIONS = ("explicit_request", "onboarding_continued")


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _text(value, field, maximum=200):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f"{field} must be nonempty text of at most {maximum} characters")
    return value.strip()


def _slot(weekly, args):
    clock = args.get("time", weekly.get("time") or "20:00")
    if not isinstance(clock, str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", clock):
        raise ValueError("time must use the 24-hour HH:MM format")
    name = args.get("timezone", weekly.get("timezone"))
    if name is None:
        return clock, None
    name = _text(name, "timezone", 100)
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise ValueError("timezone must be an available IANA timezone, such as Europe/London") from error
    return clock, name


def _sunday_clock(value):
    if isinstance(value, dict):
        if value.get("kind") != "cron":
            return None
        value = value.get("expr")
    if not isinstance(value, str):
        return None
    words = value.strip().split()
    if (len(words) == 5 and words[2:4] == ["*", "*"]
            and words[4].upper() in {"0", "7", "SUN"}
            and words[0].isdigit() and words[1].isdigit()):
        minute, hour = int(words[0]), int(words[1])
    else:
        match = re.fullmatch(
            r"(?:every\s+)?sun(?:day)?s?\s+(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)?",
            value.strip(), re.IGNORECASE)
        if not match:
            return None
        hour, minute = int(match[1]), int(match[2] or 0)
        if match[3]:
            if not 1 <= hour <= 12:
                return None
            hour = hour % 12 + (12 if match[3].lower() == "pm" else 0)
    return (hour, minute) if 0 <= hour <= 23 and 0 <= minute <= 59 else None


def _affinity(job):
    skills = job.get("skills", [])
    skills = skills if isinstance(skills, list) else []
    if job.get("skill") == "trurl:trurl" or "trurl:trurl" in skills:
        return _sunday_clock(job.get("schedule")) is not None
    name = str(job.get("name") or "")
    return bool(re.search(r"\btrurl\b", name, re.I) and re.search(r"\bsunday\b", name, re.I))


def _native(scheduler):
    unavailable = {"available": False, "jobs": [], "gateway_running": None,
                   "error": "Native scheduler inspection is unavailable."}
    if scheduler is None:
        return unavailable
    try:
        result = scheduler()
    except Exception:
        return unavailable
    if (not isinstance(result, dict) or result.get("success") is not True
            or not isinstance(result.get("jobs"), list)
            or any(not isinstance(job, dict) for job in result["jobs"])):
        return unavailable
    return {"available": True, "jobs": result["jobs"],
            "gateway_running": result.get("gateway_running"),
            "warning": result.get("warning") if isinstance(result.get("warning"), str) else None}


def _observe(weekly, native, job_id=None):
    job_id = job_id or weekly.get("automation_id") or weekly.get("job_id")
    observed = {"active": False, "job_id": job_id, "checked_at": _now(),
                "scheduler_available": native["available"],
                "gateway_running": native.get("gateway_running"),
                "warning": native.get("warning"), "timezone_verified": False,
                "delivery_verified": False}
    if not native["available"]:
        observed["reason"] = native["error"]
        return observed
    matches = [job for job in native["jobs"] if (job.get("job_id") or job.get("id")) == job_id] if job_id else []
    if len(matches) != 1:
        observed["reason"] = "No unique native job matches the recorded Trurl schedule."
        return observed
    job = matches[0]
    observed["native_job"] = {key: job[key] for key in (
        "name", "schedule", "timezone", "enabled", "state", "paused_at", "next_run_at",
        "deliver", "repeat", "last_delivery_error", "last_delivery_unverified", "last_fire_error"
    ) if key in job}
    if not _affinity(job):
        observed["reason"] = "The native job is not identifiable as a Trurl Sunday edition."
        return observed
    if job.get("enabled") is not True or job.get("state") not in {"scheduled", "running"} or job.get("paused_at"):
        observed["reason"] = "The native Trurl job is paused, disabled or not scheduled."
        return observed
    if job.get("repeat") != "forever":
        observed["reason"] = "The native job is not an ongoing weekly schedule."
        return observed
    try:
        clock, name = _slot(weekly, {})
    except ValueError:
        clock, name = None, None
    if not name:
        observed["reason"] = "A valid local timezone is required before confirming Sunday delivery."
        return observed
    requested_clock = tuple(map(int, clock.split(":")))
    if _sunday_clock(job.get("schedule")) != requested_clock:
        observed["reason"] = "The native job does not match the requested Sunday time."
        return observed
    if job.get("timezone"):
        if job["timezone"] != name:
            observed["reason"] = "The native job timezone differs from the requested timezone."
            return observed
        observed["timezone_verified"] = True
    try:
        next_run = datetime.fromisoformat(str(job.get("next_run_at") or "").replace("Z", "+00:00"))
        if next_run.tzinfo is None:
            raise ValueError("naive timestamp")
        local = next_run.astimezone(ZoneInfo(name))
        if local.weekday() != 6 or (local.hour, local.minute) != requested_clock:
            raise ValueError("different local slot")
    except (ValueError, TypeError, OverflowError):
        observed["reason"] = "The native next-run timestamp does not confirm the requested local Sunday slot."
        return observed
    observed["next_run_local"] = local.isoformat()
    if not observed["timezone_verified"]:
        observed["timezone_limit"] = (
            "Hermes returned the matching next occurrence but not its timezone pin. "
            "The host must explicitly set the requested IANA timezone; future daylight-saving behaviour is not verified.")
    if native.get("gateway_running") is not True:
        observed["reason"] = "The Hermes scheduler gateway is stopped or its liveness could not be verified."
        return observed
    observed.update(active=True, reason=None, activation_scope="native_schedule")
    observed["delivery_target"] = job.get("deliver", "local")
    if observed["delivery_target"] in (None, "", "local"):
        observed["delivery_note"] = "The native job saves results locally; message delivery is not configured."
    else:
        observed["delivery_note"] = "The native delivery target is configured; successful delivery has not been verified."
    return observed


def _result(weekly, native, observation=None):
    observation = observation or _observe(weekly, native)
    return {"weekly_enabled": weekly.get("enabled") is True,
            "onboarding_default_pending": weekly.get("onboarding_default_pending") is True,
            "day": "sunday", "time": weekly.get("time", "20:00"), "timezone": weekly.get("timezone"),
            "delivery_active": weekly.get("enabled") is True and observation["active"],
            "native": observation, "native_job_writes_performed": False}


def schedule_tool(args, state, data_dir, scheduler=None):
    """Return ``(public_result, changed)`` and edit only this state's profile.

    ``onboarding_continued`` is the host's assertion that ``user_words`` are a
    subsequent profile-setup reply after the notice, not a memory-only answer.
    The notice itself never supplies that authorization. Native job writes are
    the host's separate responsibility under its own approval rules.
    """
    if not isinstance(args, dict):
        raise ValueError("Schedule arguments must be an object")
    action = args.get("action", "status")
    if action not in ACTIONS:
        raise ValueError("Unknown schedule action")
    profile = state["profile"]
    original = profile.get("delivery", {}).get("weekly", {})
    weekly = deepcopy(original)
    native = _native(scheduler)
    if action == "status":
        return _result(weekly, native), False

    if action == "notice":
        if weekly.get("enabled") is True or weekly.get("onboarding_default_pending") is not True:
            result = _result(weekly, native)
            result["notice_issued"] = False
            result["message"] = "Keep this existing profile's Sunday choice. A new schedule needs an explicit request."
            return result, False
        existing = [job for job in native["jobs"] if _affinity(job)]
        if existing or not native["available"]:
            weekly["onboarding_default_pending"] = False
            result = _result(weekly, native)
            result.update(notice_issued=False, existing_job_ids=[job.get("job_id") or job.get("id") for job in existing],
                          message="Automatic setup stopped: inspect the existing Trurl jobs or scheduler availability. On-demand editions remain available.")
        else:
            clock, name = _slot(weekly, args)
            if name is None:
                result = _result(weekly, native)
                result.update(notice_issued=False, requires_timezone=True,
                              message="Provide the person's local IANA timezone before disclosing Sunday delivery.")
                return result, False
            notice = {"id": uuid4().hex, "issued_at": _now(), "day": "sunday", "time": clock, "timezone": name}
            weekly.update(time=clock, timezone=name, requires_timezone=False, onboarding_notice=notice)
            result = _result(weekly, native)
            result.update(notice_issued=True, notice_id=notice["id"],
                          message=f"Sunday editions are included at {clock} ({name}) where Hermes can schedule them. Each edition runs as an ordinary request on your plan, using your current preferences. Say ‘one-off only’ to skip weekly delivery. A schedule will be set up only after you continue profile setup.",
                          native_schedule={"schedule": f"{int(clock[3:])} {int(clock[:2])} * * 0",
                                           "timezone": name, "name": "Trurl Sunday edition", "skills": ["trurl:trurl"]})
    elif action == "confirm":
        authorization = args.get("authorization")
        if authorization not in AUTHORIZATIONS:
            raise ValueError("Confirm needs explicit_request or onboarding_continued authorization")
        user_words = _text(args.get("user_words"), "user_words", 4000)
        job_id = _text(args.get("job_id"), "job_id")
        clock, name = _slot(weekly, args)
        if name is None:
            raise ValueError("Confirm requires the person's local IANA timezone")
        if authorization == "onboarding_continued":
            notice = weekly.get("onboarding_notice", {})
            if (weekly.get("onboarding_default_pending") is not True or not notice.get("id")
                    or args.get("notice_id") != notice["id"]):
                raise ValueError("Onboarding confirmation needs this profile's earlier notice and pending enrollment")
            if (clock, name) != (notice.get("time"), notice.get("timezone")):
                raise ValueError("The confirmed schedule must match the notice the person received")
        weekly.update(day="sunday", time=clock, timezone=name, requires_timezone=False)
        observation = _observe(weekly, native, job_id)
        weekly.update(enabled=observation["active"], onboarding_default_pending=False,
                      last_schedule_check=observation)
        if observation["active"]:
            weekly.update(automation_id=job_id, schedule_confirmed_at=_now())
        weekly["last_schedule_authorization"] = {"kind": authorization, "user_words": user_words,
                                                "notice_id": args.get("notice_id"), "recorded_at": _now()}
        result = _result(weekly, native, observation)
        result.update(confirmed=observation["active"],
                      message="Native Sunday schedule confirmed." if observation["active"] else observation["reason"])
    else:
        user_words = _text(args.get("user_words"), "user_words", 4000)
        weekly.update(enabled=False, onboarding_default_pending=False,
                      last_schedule_authorization={"kind": action, "user_words": user_words, "recorded_at": _now()})
        observation = _observe(weekly, native)
        result = _result(weekly, native, observation)
        known_id = weekly.get("automation_id") or weekly.get("job_id")
        existing = [job for job in native["jobs"] if _affinity(job)]
        needs_pause = [job.get("job_id") or job.get("id") for job in existing
                       if job.get("enabled") is True and not job.get("paused_at") and job.get("state") != "paused"]
        result.update(pause_job_ids=needs_pause, native_pause_confirmed=False,
                      message="Sunday curation is off in Trurl. The host must pause any still-active native job.",
                      native_pause_check_required=not native["available"] or bool(needs_pause),
                      recorded_job_id=known_id)

    changed = weekly != original
    if changed:
        profile.setdefault("delivery", {})["weekly"] = weekly
        profile["updated_at"] = _now()
    return result, changed


def check_schedule(profile, context, scheduler=None):
    """Fail closed for weekly planning and committing; on-demand needs no cron."""
    if context == "on_demand":
        return
    if context != "weekly":
        raise ValueError("context must be on_demand or weekly")
    weekly = profile.get("delivery", {}).get("weekly", {})
    if weekly.get("enabled") is not True:
        raise ValueError("Weekly Trurl delivery is disabled")
    observation = _observe(weekly, _native(scheduler))
    if not observation["active"]:
        raise ValueError(observation["reason"])
