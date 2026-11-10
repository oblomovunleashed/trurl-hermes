"""Hermes-native orchestration for the bundled Trurl planner and renderer."""

from contextlib import closing
from copy import deepcopy
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import secrets
import sqlite3
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit
from uuid import uuid4

from .engine.scripts import curation, diversity, sunday
from . import memory, scheduling, preferences

CATEGORY_IDS = tuple(sunday.CATEGORIES)
PICK_FIELDS = ("medium", "title", "creator", "canonical_url", "description", "detail")
REACTIONS = ("liked", "disliked", "already_known", "more_like_this", "would_try", "completed", "skipped",
             "confirmed_interest", "rejected_interest", "too_introductory", "want_less", "correction")
HISTORY_SCORE_FIELDS = ("scoring_basis", "scoring_weights", "personal_fit_status", "value_rationale",
                        "source_family", "discovery_domain")


def _recording_score_fields(item):
    """An open-value score must never become a personal six-axis fit score."""
    return {"weighted_total": item["base_score"] if item.get("scoring_basis") == "six_axis_weighted_fit" else None,
            **{field: deepcopy(item[field]) for field in HISTORY_SCORE_FIELDS if field in item}}


def _repair_recorded_scores(state):
    """Recover old delivery labels only from an unambiguous saved selection."""
    for record in state["history"]:
        batch = state["batches"].get(record.get("batch_id"), {})
        selection = batch.get("selection_plan", {})
        matches = [item for item in selection.get("selected", [])
                   if isinstance(item, dict) and all(item.get(field) == record.get(field)
                                                    for field in ("canonical_url", "title", "creator", "medium"))]
        if (len(matches) == 1 and "base_score" in matches[0]
                and matches[0].get("scoring_basis") in {"six_axis_weighted_fit", "open_value_without_personal_relevance"}):
            record.update(_recording_score_fields(matches[0]))


def _text(value, name, maximum, optional=False):
    if not isinstance(value, str):
        raise ValueError(f"{name} must be text")
    value = " ".join(value.split())
    if len(value) > maximum or (not value and not optional):
        raise ValueError(f"{name} must contain {0 if optional else 1}–{maximum} characters")
    return value


def _categories(values):
    if (not isinstance(values, list) or not values
            or any(value not in CATEGORY_IDS for value in values)
            or len(set(values)) != len(values)):
        raise ValueError("Choose distinct categories from book, film, podcast, youtube_video, lecture, scientific_paper")
    return list(values)


def _boolean(args, key, default=False):
    value = args.get(key, default)
    if type(value) is not bool:
        raise ValueError(f"{key} must be true or false")
    return value


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _interest(topic, origin, explicit=True):
    digest = hashlib.sha256(topic.casefold().encode()).hexdigest()[:16]
    return {"id": f"interest-{digest}", "topic": topic, "kind": "explicit" if explicit else "inferred",
            "signal": "interested", "confidence": 1 if explicit else 0.5, "knowledge_level": "unknown",
            "scope": "persistent", "source_provenance": [origin]}


def _add_anchor(profile, topic, origin, explicit=True):
    """Keep direct evidence independent of a hypothesis's managed projection."""
    existing = next((entry for entry in profile["interests"]
                     if entry["topic"].casefold() == topic.casefold()
                     and not entry.get("hypothesis_id")
                     and not str(entry.get("id", "")).startswith("hypothesis:")), None)
    if existing is None:
        profile["interests"].append(_interest(topic, origin, explicit=explicit))
        return True
    if explicit:
        existing.update(kind="explicit", confidence=1)
    if origin not in existing.setdefault("source_provenance", []):
        existing["source_provenance"].append(origin)
    return False


def _initial_state():
    return {"schema_version": 3, "profile": curation.generic_profile(), "plans": {}, "batches": {},
            "history": [], "feedback": {}, "graph": memory.graph.empty_graph()}


def _unlocated_feedback_source():
    return {"kind": "current_conversation", "id": "unlocated-current-conversation"}


def _migrate_feedback_sources(state):
    """Separate generated event locations from evidence of an original source."""
    for key, record in state["feedback"].items():
        locator = "sqlite:feedback/" + key
        sources = record.get("source_provenance")
        if any(sources == [{"kind": kind, "locator": locator}]
               for kind in ("user_feedback", "legacy_plugin_feedback")):
            record["event_locator"] = locator
            record["source_provenance"] = [_unlocated_feedback_source()]


def _migrate_state(state):
    """Upgrade old records without treating delivery or silence as enjoyment."""
    if state.get("schema_version") == 3:
        return False
    if state.get("schema_version") == 2:
        _migrate_feedback_sources(state)
        _repair_recorded_scores(state)
        state["schema_version"] = 3
        return True
    if state.get("schema_version", 1) != 1:
        raise ValueError("Unsupported Trurl state version")
    profile = state["profile"]
    defaults = curation.generic_profile()
    profile.setdefault("onboarding", defaults["onboarding"])
    profile["onboarding"].setdefault("context_sources", defaults["onboarding"]["context_sources"])
    profile.setdefault("provenance", [])
    weekly = profile["delivery"]["weekly"]
    weekly["legacy_consent_enabled"] = bool(weekly.get("enabled"))
    weekly["enabled"] = False
    weekly["onboarding_default_pending"] = False
    weekly["migration_note"] = "Legacy preference retained; confirm an actual native job before scheduled runs."
    for key, record in state["feedback"].items():
        record.setdefault("id", key)
        record.setdefault("signal", {"already_known": "already_knew", "more_like_this": "want_more"}.get(record.get("reaction"), record.get("reaction")))
        record.setdefault("scope", "item")
        record.setdefault("user_words", record.get("note") or None)
        record.setdefault("source_provenance", [{"kind": "legacy_plugin_feedback", "locator": "sqlite:feedback/" + key}])
        record.setdefault("profile_changes", [])
    for record in state["history"]:
        record.setdefault("engagement", "unknown")
        record.setdefault("source_provenance", [{"kind": "legacy_plugin_delivery", "locator": "sqlite:history/" + record["id"]}])
    known = {item.get("id") for item in profile["provenance"]}
    for interest in profile["interests"]:
        for ref in interest.get("source_provenance", []):
            if isinstance(ref, str) and ref not in known:
                profile["provenance"].append({"id": ref, "kind": "legacy_plugin_record", "locator": "sqlite:profile/interests", "captured_at": None})
                known.add(ref)
    _migrate_feedback_sources(state)
    _repair_recorded_scores(state)
    state["schema_version"] = 3
    return True


def _taste_fingerprint(state):
    return hashlib.sha256(json.dumps({"profile": state["profile"], "feedback": state["feedback"],
                                     "brief": state.get("brief")},
                                    sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def _brief_trace(info):
    if info["status"] == "fresh":
        return {key: info[key] for key in ("status", "version", "file_sha256")}
    return {"status": "raw_model_fallback", "reason": "; ".join(info.get("reasons", [])) or info["status"]}


def _planning_evidence(state):
    """Allow the retained review to add hypotheses, not replace original evidence."""
    profile = deepcopy(state["profile"])
    profile.pop("updated_at", None)
    profile.pop("taste_hypotheses", None)
    profile["interests"] = [item for item in profile.get("interests", [])
                            if not item.get("hypothesis_id")
                            and not str(item.get("id", "")).startswith("hypothesis:")]
    return hashlib.sha256(json.dumps({"profile": profile, "feedback": state["feedback"],
                                     "history": state["history"]}, sort_keys=True,
                                    ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def _taste(args, state, data_dir):
    profile = state["profile"]
    before = deepcopy(profile)
    onboarding = profile["onboarding"]
    source = args.get("source", "current_conversation")
    if not isinstance(source, str) or source not in {"current_conversation", "agent_memory", "past_chats"}:
        raise ValueError("Unknown preference source")
    if source != "current_conversation" and set(args) - {"source", "interests", "brain_dump", "source_note"}:
        raise ValueError("Imported sources can add scoped evidence only; settings and corrections require the current user")
    if "context_sources" in args:
        choices = args["context_sources"]
        if not isinstance(choices, dict) or not set(choices).issubset({"agent_memory", "past_chats"}):
            raise ValueError("context_sources supports agent_memory and past_chats separately")
        for source_name, choice in choices.items():
            if not isinstance(choice, dict) or not set(choice).issubset({"permission", "scope", "availability"}):
                raise ValueError("Source choices support permission, scope and actual availability")
            record = onboarding["context_sources"][source_name]
            if "permission" in choice:
                if choice["permission"] not in {"allowed", "declined", "not_asked"}:
                    raise ValueError("Invalid context-source permission")
                record["permission"] = choice["permission"]
            if "availability" in choice:
                if choice["availability"] not in {"available", "partial", "unavailable", "unknown"}:
                    raise ValueError("Invalid context-source availability")
                record["availability"] = choice["availability"]
            if "scope" in choice:
                record["scope"] = _text(choice["scope"], "source scope", 2000, optional=True)
    if source != "current_conversation" and any(key in args for key in ("interests", "brain_dump")):
        choice = onboarding["context_sources"][source]
        if choice["permission"] != "allowed" or choice["availability"] not in {"available", "partial"} or not choice.get("scope"):
            raise ValueError("This source needs separate permission, a scope and actual availability")
        _text(args.get("source_note", ""), "source_note for imported evidence", 2000)
    if "weekly_enabled" in args:
        if _boolean(args, "weekly_enabled"):
            raise ValueError("Use the native scheduler, then trurl_schedule confirm; a preference cannot prove an active job")
        profile["delivery"]["weekly"].update(enabled=False, onboarding_default_pending=False)
    if "brain_dump" in args or "interests" in args:
        raw = _text(args.get("brain_dump", ""), "brain_dump", 40000, optional=True)
        values = args.get("interests", [])
        if not isinstance(values, list) or len(values) > 200:
            raise ValueError("interests must contain at most 200 supplied anchors per call; add more in another call")
        values = [_text(value, "interest", 500) for value in values]
        note = _text(args.get("source_note", ""), "source_note", 2000, optional=True)
        origin = "source-" + hashlib.sha256(json.dumps([source, raw, values, note], ensure_ascii=False).encode()).hexdigest()[:20]
        if not any(item.get("id") == origin for item in profile["provenance"]):
            record = {"id": origin, "kind": source, "locator": note or "current Hermes conversation",
                      "captured_at": _now(), "user_words": raw or None}
            if source != "current_conversation":
                record["source_choice"] = deepcopy(onboarding["context_sources"][source])
            profile["provenance"].append(record)
    if "interests" in args:
        if _boolean(args, "replace_interests"):
            profile["interests"] = []
        for topic in values:
            _add_anchor(profile, topic, origin, explicit=source != "agent_memory")
    elif args.get("replace_interests"):
        raise ValueError("replace_interests requires an interests list")
    if "remove_interest_ids" in args:
        remove = args["remove_interest_ids"]
        if not isinstance(remove, list) or any(not isinstance(item, str) for item in remove):
            raise ValueError("remove_interest_ids must be an array of actual interest IDs")
        if not set(remove).issubset({item["id"] for item in profile["interests"]}):
            raise ValueError("Unknown interest ID")
        if any(item.startswith("hypothesis:") for item in remove):
            raise ValueError("Retire or correct the underlying hypothesis with trurl_hypotheses")
        profile["interests"] = [item for item in profile["interests"] if item["id"] not in remove]
    if "categories" in args:
        profile["media"]["enabled"] = _categories(args["categories"])
        onboarding["categories"] = profile["media"]["enabled"]
    if any(key in args for key in ("interests", "brain_dump", "categories")):
        onboarding["status"] = "ready" if profile["interests"] else "starter"
    if "preferences" in args:
        preferences.apply_preferences(args, state)
    changed = profile != before
    if changed:
        profile["updated_at"] = _now()
        memory.reconcile(state, data_dir)
        profile = state["profile"]
    sync, synced = memory.sync_graph(state, data_dir)
    changed = changed or synced
    return {
        "interests": [record["topic"] for record in profile["interests"]],
        "categories": profile["media"]["enabled"],
        "weekly_enabled": profile["delivery"]["weekly"]["enabled"],
        "baseline_weights": profile["taste"]["axes_weights"],
        "recommendation_count": len(state["history"]),
        "feedback_count": len(state["feedback"]),
        "profile": deepcopy(profile),
        "storage": {"database": str(data_dir / "trurl.sqlite3"), "scope": "active Hermes profile; persists across sessions"},
        "curators_brief": memory.brief_info(state, data_dir),
        "graph_anchors": sync,
        "hypothesis_maintenance": memory.maintenance(state, data_dir, event="onboarding"),
    }, changed


def _sunday_date(value):
    if not isinstance(value, str):
        raise ValueError("edition_date must be a Sunday in YYYY-MM-DD form")
    day = date.fromisoformat(value)
    if day.isoformat() != value or day.weekday() != 6:
        raise ValueError("edition_date must be a Sunday in YYYY-MM-DD form")
    return value


def _check_schedule(profile, context, scheduler=None):
    scheduling.check_schedule(profile, context, scheduler)


def _plan(args, state, data_dir, scheduler=None):
    memory._arguments(args, {"request", "plan_id", "context", "categories", "edition_date", "count", "mode", "scope", "fixed"})
    if "plan_id" in args:
        if set(args) != {"plan_id"}:
            raise ValueError("Resume with plan_id only; a changed request needs a new plan")
        plan_id = _text(args["plan_id"], "plan_id", 32)
        pending = state["plans"].get(plan_id)
        if pending is None or pending.get("workflow_version") != 3:
            raise ValueError("Unknown or legacy plan_id; make a fresh plan")
        _check_schedule(state["profile"], pending["plan"]["context"], scheduler)
        if pending.get("planning_evidence") != _planning_evidence(state):
            raise ValueError("Original taste evidence changed; make a fresh plan")
        if pending.get("discovery") is not None and pending.get("taste_fingerprint") != _taste_fingerprint(state):
            raise ValueError("Taste evidence changed after discovery; make a fresh plan")
        return _finish_plan(plan_id, pending, state, data_dir)
    request = _text(args.get("request"), "request", 1200)
    profile = state["profile"]
    context = args.get("context", "on_demand")
    _check_schedule(profile, context, scheduler)
    categories = _categories(args.get("categories", profile["media"]["enabled"]))
    edition_date = _sunday_date(args["edition_date"]) if "edition_date" in args else None
    if context == "weekly" and not edition_date:
        raise ValueError("A weekly issue needs its local Sunday date")
    count = args.get("count", len(categories) if edition_date else 3)
    if type(count) is not int or not 1 <= count <= 12:
        raise ValueError("count must be an integer from 1 to 12")
    if edition_date and count != len(categories):
        raise ValueError("A Sunday edition has one slot per chosen category")
    repeat_count = 0
    for batch in reversed(list(state["batches"].values())):
        if (batch["plan"]["context"] != "on_demand" or batch["plan"]["scope"] != "broad"
                or batch["categories"] != categories):
            break
        repeat_count += 1
    weights = curation.make_plan(profile, request, context=context, mode=args.get("mode", "auto"),
                                 scope=args.get("scope", "auto"), fixed=_boolean(args, "fixed"),
                                 repeat_count=repeat_count)
    plan_id = uuid4().hex
    plans = state["plans"]
    plans[plan_id] = {"plan": weights, "discovery": None, "categories": categories, "count": count,
                      "edition_date": edition_date, "selection_seed": secrets.randbits(64),
                      "discovery_seed": secrets.randbits(64), "workflow_version": 3,
                      "planning_evidence": _planning_evidence(state)}
    if len(plans) > 32:
        del plans[next(iter(plans))]
    return _finish_plan(plan_id, plans[plan_id], state, data_dir)


def _finish_plan(plan_id, pending, state, data_dir):
    profile = state["profile"]
    review = memory.maintenance(state, data_dir, pending["plan"])
    if not review["review_due"] and pending.get("discovery") is None:
        memory.sync_graph(state, data_dir)
        checked = memory.maintenance(state, data_dir, pending["plan"], check=True)
        pending["discovery"] = diversity.explore(
            pending["plan"], profile, history=state["history"], feedback=list(state["feedback"].values()),
            graph=state.get("graph"), count=max(5, pending["count"]), seed=pending["discovery_seed"],
            maintenance=checked, anchor_mappings=state.get("graph_anchors"))
        pending["taste_fingerprint"] = _taste_fingerprint(state)
        pending["curators_brief"] = _brief_trace(memory.brief_info(state, data_dir))
    compact, unused = memory.context_tool({}, state, data_dir)
    return {"plan_id": plan_id, "weights": pending["plan"], "discovery": pending["discovery"],
            "categories": pending["categories"], "count": pending["count"], "edition_date": pending["edition_date"],
            "hypothesis_maintenance": review,
            "curators_brief": compact["brief"], "interest_inventory": compact["interests"],
            "lasting_constraints": deepcopy(profile["taste"].get("constraints", [])),
            "presentation": deepcopy(profile["taste"].get("presentation", {}))}, True


def _edition_files(data_dir, plan_id, edition_date, categories, picks):
    issue = {"edition_date": edition_date, "categories": categories, "picks": picks}
    encoded = json.dumps(issue, ensure_ascii=False).encode("utf-8")
    suffix = hashlib.sha256(encoded).hexdigest()[:12]
    target = data_dir / "editions" / f"{plan_id}-{suffix}"
    filenames = {"svg": "sunday-edition.svg", "markdown": "sunday-edition.md"}
    if not all((target / name).is_file() for name in filenames.values()):
        with TemporaryDirectory(prefix=".trurl-input-", dir=data_dir) as temp:
            source = Path(temp) / "issue.json"
            source.write_bytes(encoded)
            sunday.render_edition(source, target)
    return {kind: str(target / filename) for kind, filename in filenames.items()}


def _public_link(value):
    if not isinstance(value, str) or any(character.isspace() or character in '<>\\"' for character in value):
        return False
    try:
        url = urlsplit(value)
        return url.scheme == "https" and bool(url.hostname) and url.username is None and url.password is None
    except ValueError:
        return False


def _graph(args, state, data_dir, scheduler=None):
    if args.get("action", "sync") not in {"sync", "records"}:
        pending = state["plans"].get(args.get("plan_id")) if isinstance(args.get("plan_id"), str) else None
        if pending is None or pending.get("workflow_version") != 3:
            raise ValueError("Graph review requires a current pending plan_id")
        _check_schedule(state["profile"], pending["plan"]["context"], scheduler)
        if pending.get("taste_fingerprint") != _taste_fingerprint(state):
            raise ValueError("Taste evidence changed; make a fresh plan before graph review")
        checked = memory.maintenance(state, data_dir, pending["plan"], check=True)
        if checked["eligible"] and not checked["completed"]:
            raise ValueError("Complete current hypothesis maintenance before graph review")
    return memory.graph_tool(args, state, data_dir)


def _select(args, state, data_dir, scheduler=None):
    memory._arguments(args, {"plan_id", "candidates", "commit"})
    plan_id = _text(args.get("plan_id"), "plan_id", 32)
    commit = _boolean(args, "commit")
    if plan_id in state["batches"]:
        batch = state["batches"][plan_id]
        _check_schedule(state["profile"], batch["plan"]["context"], scheduler)
        result = dict(batch["result"], already_recorded=True)
        if batch["edition_date"]:
            result["edition_files"] = _edition_files(
                data_dir, plan_id, batch["edition_date"], batch["categories"], result["picks"])
        return result, False
    pending = state["plans"].get(plan_id)
    if pending is None:
        raise ValueError("Unknown plan_id. Make a fresh plan with trurl_plan.")
    _check_schedule(state["profile"], pending["plan"]["context"], scheduler)
    if pending.get("workflow_version") != 3 or pending.get("discovery") is None:
        raise ValueError("Complete hypothesis maintenance and resume a current plan before selection")
    maintenance = memory.maintenance(state, data_dir, pending["plan"], check=True)
    if maintenance["eligible"] and not maintenance["completed"]:
        raise ValueError("Hypothesis maintenance is due; complete it before selection")
    if pending.get("taste_fingerprint") != _taste_fingerprint(state):
        raise ValueError("Taste evidence changed, or this is a legacy plan. Use trurl_plan for a fresh request plan.")
    if pending.get("curators_brief") != _brief_trace(memory.brief_info(state, data_dir)):
        raise ValueError("The curator's brief changed or became stale. Use trurl_plan for a fresh request plan.")
    candidates = args.get("candidates")
    if not isinstance(candidates, list) or len(candidates) > 96 or not all(isinstance(c, dict) for c in candidates):
        raise ValueError("candidates must be a list of at most 96 researched candidate objects")
    if len(json.dumps(candidates, ensure_ascii=False).encode("utf-8")) > 256_000:
        raise ValueError("Candidate metadata exceeds 256 kB")
    # Keep the exact reviewed research separately from the filtered/normalized
    # selector input so its completion fingerprint remains reproducible.
    research_pool = deepcopy(candidates)
    graph_receipt = memory.graph_check(state, data_dir, research_pool, plan_id)
    if not graph_receipt["completed"]:
        raise ValueError("Graph update is pending; use trurl_graph plan and complete for this exact candidate pool")
    categories = pending["categories"]
    edition_date = pending["edition_date"]
    candidates = [dict(candidate) for candidate in candidates
                  if candidate.get("medium") in categories and _public_link(candidate.get("canonical_url"))]
    for candidate in candidates:
        hypothesis_id = candidate.get("hypothesis_id")
        if hypothesis_id is not None:
            hypothesis_id = _text(hypothesis_id, "hypothesis_id", 160)
            candidate["hypothesis_id"] = hypothesis_id
        if hypothesis_id and hypothesis_id not in {entry.get("hypothesis_id") for entry in pending["discovery"].get("briefs", [])}:
            raise ValueError("A hypothesis candidate must use the hypothesis retained in this plan's discovery brief")
        for name, limit in (("title", 280), ("creator", 160), ("canonical_url", 2048),
                            ("description", 280), ("detail", 120)):
            if name in candidate:
                candidate[name] = _text(candidate[name], name, limit, optional=name in ("description", "detail"))
    if len(json.dumps(candidates, ensure_ascii=False).encode("utf-8")) > 256_000:
        raise ValueError("Candidate metadata exceeds 256 kB")
    selection = diversity.select_slate(
        pending["plan"], candidates, pending["count"], history=state["history"],
        seed=pending["selection_seed"], categories=categories if edition_date else None)
    selected = selection["selected"]
    if edition_date:
        selected = sorted(selected, key=lambda item: categories.index(item["medium"]))
    picks = []
    for item in selected:
        pick = {field: item[field] for field in PICK_FIELDS if field in item}
        if commit:
            pick["recommendation_id"] = f"{plan_id}-{len(picks) + 1}"
        picks.append(pick)
    result = {"plan_id": plan_id, "picks": picks, "shortfall": selection["shortfall"],
              "missing_categories": selection.get("missing_categories", []),
              "edition_date": edition_date, "recorded": commit}
    if not commit:
        return result, False
    if edition_date:
        result["edition_files"] = _edition_files(data_dir, plan_id, edition_date, categories, picks)
    state["history"].extend(
        {**pick, "id": pick["recommendation_id"], "recommended_at": _now(), "status": "recommended",
         "batch_id": plan_id, "engagement": "unknown", "lane": item["lane"], "topics": item["topics"],
         "verification": item["verification"], "scores": item["scores"], **_recording_score_fields(item),
         "personal_connection": item.get("personal_connection"), "hypothesis_id": item.get("hypothesis_id"),
         "source_provenance": [{"kind": "hermes_tool_delivery", "locator": "sqlite:batches/" + plan_id}]}
        for item, pick in zip(selected, picks)
    )
    state["batches"][plan_id] = {**pending, "result": result,
                                "selected_scores": [item["scores"] for item in selected],
                                "research_pool": research_pool, "candidate_pool": candidates,
                                "selection_plan": selection,
                                "diversity_audit": selection["audit"],
                                "hypothesis_maintenance": maintenance, "graph_update": graph_receipt}
    del state["plans"][plan_id]
    return result, True


def _feedback_sources(args, profile):
    """Validate original-source identities and derive a stable retry key."""
    if "source_provenance" not in args:
        return [_unlocated_feedback_source()], None
    sources = args["source_provenance"]
    if not isinstance(sources, list) or not 1 <= len(sources) <= 8:
        raise ValueError("source_provenance needs one to eight original source IDs or locator objects")
    limits = {"id": 500, "kind": 80, "locator": 2000, "message_id": 500, "thread_id": 500,
              "captured_at": 80, "recorded_at": 80}
    validated = []
    for source in sources:
        if isinstance(source, str):
            validated.append(_text(source, "feedback source ID", 500))
        elif isinstance(source, dict) and source and not set(source) - set(limits):
            item = {key: _text(value, "feedback source " + key, limits[key]) for key, value in source.items()}
            if not any(item.get(key) for key in ("id", "message_id", "locator")):
                raise ValueError("Feedback sources need an original source ID, message_id or locator")
            validated.append(item)
        else:
            raise ValueError("Feedback sources must be source IDs or bounded original locator objects")
    provenance = {item["id"]: item for item in profile.get("provenance", [])
                  if isinstance(item, dict) and isinstance(item.get("id"), str)}
    resolved = [provenance.get(source, source) if isinstance(source, str) else source for source in validated]
    for source in resolved:
        locator = source.get("locator", "") if isinstance(source, dict) else source
        if isinstance(locator, str) and locator.startswith("sqlite:feedback/"):
            raise ValueError("A stored feedback event is not an identifiable original source")
    keys = memory.hypotheses._source_keys({"source_provenance": resolved})
    return validated, sorted(keys)


def _feedback(args, state, data_dir):
    recommendation_id = args.get("recommendation_id")
    if recommendation_id is not None:
        recommendation_id = _text(recommendation_id, "recommendation_id", 64)
    recommended = next((item for item in state["history"] if item["id"] == recommendation_id), None)
    if recommendation_id is not None and recommended is None:
        raise ValueError("Feedback needs a recommendation_id from a recorded pick")
    scope = args.get("scope", "item")
    if not isinstance(scope, str) or scope not in {"item", "topic", "creator", "presentation", "global", "current_request"}:
        raise ValueError("Choose feedback scope item, topic, creator, presentation, global or current_request")
    if scope == "item" and recommended is None:
        raise ValueError("Item feedback needs a recommendation_id from a recorded pick")
    target = _text(args.get("target", ""), "feedback target", 500, optional=scope == "item")
    reaction = args.get("reaction")
    if reaction not in REACTIONS:
        raise ValueError("Choose a supported explicit reaction")
    note = _text(args.get("note", ""), "note", 500, optional=True)
    if scope != "item" and not note:
        raise ValueError("Scoped feedback needs the user's actual words")
    interest = _text(args.get("interest", ""), "interest", 160, optional=True)
    if interest and reaction != "more_like_this":
        raise ValueError("A new interest needs an explicit more_like_this request")
    if interest and scope == "current_request":
        raise ValueError("A request-only correction cannot add a lasting interest")
    hypothesis_id = args.get("hypothesis_id", (recommended or {}).get("hypothesis_id"))
    if hypothesis_id is not None:
        hypothesis_id = _text(hypothesis_id, "hypothesis_id", 160)
    if hypothesis_id and hypothesis_id not in {item["id"] for item in state["profile"].get("taste_hypotheses", [])}:
        raise ValueError("Unknown hypothesis ID")
    if reaction in {"confirmed_interest", "rejected_interest"} and (not hypothesis_id or not note):
        raise ValueError("A broader taste confirmation needs its hypothesis_id and the user's actual words")
    sources, source_keys = _feedback_sources(args, state["profile"])
    identity = [recommendation_id, reaction, note, interest]
    if hypothesis_id:
        identity.append(hypothesis_id)
    if scope != "item" or target:
        identity.extend([scope, target])
    if source_keys is not None:
        identity.append({"original_sources": source_keys})
    key = hashlib.sha256(json.dumps(identity).encode()).hexdigest()
    if key in state["feedback"]:
        return {"recorded": True, "already_recorded": True, "interest_added": False}, False
    signal = {"already_known": "already_knew", "more_like_this": "want_more"}.get(reaction, reaction)
    state["feedback"][key] = {"id": key, "recommendation_id": recommendation_id, "reaction": reaction,
                              "signal": signal, "hypothesis_id": hypothesis_id, "user_words": note or None,
                              "scope": "interest" if reaction in {"confirmed_interest", "rejected_interest"} else scope,
                              "target": target or recommendation_id,
                              "source_provenance": sources, "event_locator": "sqlite:feedback/" + key,
                              "profile_changes": [], "note": note, "interest": interest or None, "recorded_at": _now()}
    profile = state["profile"]
    add_interest = False
    if interest:
        origin = f"feedback:{key}"
        profile["provenance"].append({"id": origin, "kind": "explicit_user_feedback", "locator": "sqlite:feedback/" + key,
                                       "captured_at": _now(), "user_words": note or interest})
        add_interest = _add_anchor(profile, interest, origin)
        profile["updated_at"] = _now()
    if hypothesis_id and note and reaction in {"liked", "disliked", "confirmed_interest", "rejected_interest"}:
        snapshot, unused = memory.hypothesis_tool({"action": "snapshot"}, state, data_dir)
        draft = snapshot["snapshot"]
        item = next(entry for entry in draft["taste_hypotheses"] if entry["id"] == hypothesis_id)
        if item.get("status") not in {"retired", "stale"}:
            broad = reaction in {"confirmed_interest", "rejected_interest"}
            if not broad and (scope != "item" or (recommended or {}).get("hypothesis_id") != hypothesis_id):
                raise ValueError("Item feedback can support only the hypothesis actually used for that delivered pick")
            item.setdefault("evidence", []).append({
                "feedback_id": key, "effect": "confirm" if reaction == "confirmed_interest" else
                    "support" if reaction == "liked" else "contradict",
                "scope": "hypothesis" if broad else "work",
                "reason": "Explicit broader preference correction." if broad else
                    "Reaction to a delivered work selected through this hypothesis; the connection remains tentative.",
            })
            memory.hypothesis_tool({"action": "save", "draft": draft}, state, data_dir)
    memory.reconcile(state, data_dir)
    sync, unused = memory.sync_graph(state, data_dir)
    return {"recorded": True, "interest_added": add_interest, "graph_anchors": sync,
            "hypothesis_maintenance": memory.maintenance(state, data_dir, event="feedback")}, True


OPERATIONS = {"trurl_taste": _taste, "trurl_plan": _plan, "trurl_select": _select,
              "trurl_feedback": _feedback, "trurl_brief": memory.brief_tool,
              "trurl_hypotheses": memory.hypothesis_tool, "trurl_atlas": memory.atlas_tool,
              "trurl_context": memory.context_tool, "trurl_graph": _graph,
              "trurl_schedule": scheduling.schedule_tool}


def invoke(data_dir, name, args, scheduler=None):
    """Run the tool in one profile-scoped transaction across Hermes processes."""
    try:
        if not isinstance(args, dict):
            raise ValueError("Tool arguments must be an object")
        if name not in OPERATIONS:
            raise ValueError("Unknown Trurl operation")
        data_dir = Path(data_dir)
        data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        with closing(sqlite3.connect(data_dir / "trurl.sqlite3", timeout=15)) as connection:
            with connection:
                connection.execute("CREATE TABLE IF NOT EXISTS curation (id INTEGER PRIMARY KEY, data TEXT NOT NULL)")
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute("SELECT data FROM curation WHERE id = 1").fetchone()
                state = json.loads(row[0]) if row else _initial_state()
                migrated = _migrate_state(state)
                reconciled = memory.reconcile(state, data_dir)
                if name in {"trurl_plan", "trurl_select", "trurl_schedule", "trurl_graph"}:
                    result, changed = OPERATIONS[name](args, state, data_dir, scheduler)
                else:
                    result, changed = OPERATIONS[name](args, state, data_dir)
                if changed or row is None or migrated or reconciled:
                    connection.execute("INSERT OR REPLACE INTO curation VALUES (1, ?)",
                                       (json.dumps(state, ensure_ascii=False, allow_nan=False),))
        return json.dumps(result, ensure_ascii=False, allow_nan=False)
    except (ValueError, OSError, sqlite3.Error, TypeError, KeyError) as error:
        return json.dumps({"error": str(error)}, ensure_ascii=False)
