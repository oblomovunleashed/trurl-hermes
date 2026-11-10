"""Adapt evidence helpers to Hermes's authoritative, profile-scoped SQLite state.

The helpers see short-lived canonical model files inside the plugin data
directory. Only reviewed state returns to SQLite; the requested Atlas is the
only durable file this adapter exports.
"""

from contextlib import contextmanager
import copy
import hashlib
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory

from .engine.scripts import atlas, brief, hypotheses, context, graph, graph_anchors, graph_updates


SOURCES = ("model/profile.json", "model/feedback.jsonl", "model/recommendations.jsonl")
MANAGED = {"brief": "curators-brief.json", "graph": "graph.json",
           "graph_anchors": "graph-anchors.json", "hypothesis_maintenance": "hypothesis-maintenance.json",
           "graph_maintenance": "graph-maintenance.json"}
EVIDENCE_LIMITS = [
    "These records come from this Hermes profile's Trurl state, not a search of agent memory or past chats.",
    "Recommendations record suggestions; only explicit feedback supplies evidence of enjoyment.",
    "The brief is a derived summary. Its author must preserve source scope, uncertainty and the full interest inventory.",
]


def _json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n"


def _feedback(state):
    records = state.get("feedback", {})
    if not isinstance(records, dict) or any(not isinstance(item, dict) for item in records.values()):
        raise ValueError("Feedback state must contain keyed feedback records")
    return list(records.values())


def _source_paths(document):
    records = document.get("sources") if isinstance(document, dict) else None
    if not isinstance(records, list) or any(
            not isinstance(item, dict) or item.get("path") not in SOURCES for item in records):
        raise ValueError("Use only the profile, feedback and recommendation sources returned by the snapshot")


def _arguments(args, allowed):
    if not isinstance(args, dict) or set(args) - set(allowed):
        raise ValueError("Unsupported memory tool arguments")


@contextmanager
def _projection(state, data_dir):
    """Materialise one consistent state; the caller holds the SQLite transaction."""
    data_dir = Path(data_dir)
    data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not isinstance(state.get("profile"), dict) or not isinstance(state.get("history"), list):
        raise ValueError("A profile object and recommendation history are required")
    feedback = _feedback(state)
    with TemporaryDirectory(prefix=".trurl-memory-", dir=data_dir) as temporary:
        root = Path(temporary)
        model = root / "model"
        model.mkdir(mode=0o700)
        (model / "profile.json").write_text(_json(state["profile"]), encoding="utf-8")
        batches = [dict(value, id=key) for key, value in state.get("batches", {}).items()]
        for filename, records in (("feedback.jsonl", feedback), ("recommendations.jsonl", state["history"]),
                                  ("batches.jsonl", batches)):
            text = "".join(json.dumps(record, ensure_ascii=False, allow_nan=False, sort_keys=True) + "\n"
                           for record in records)
            (model / filename).write_text(text, encoding="utf-8")
        for key, filename in MANAGED.items():
            if state.get(key) is not None:
                (model / filename).write_text(_json(state[key]), encoding="utf-8")
        yield root


def _read_profile(root):
    return json.loads((root / "model/profile.json").read_text(encoding="utf-8"))


def _capture(root, state, keys=()):
    """Persist only reviewed helper outputs back into the current transaction."""
    changed = False
    for key in keys:
        path = root / "model" / ("profile.json" if key == "profile" else MANAGED[key])
        if path.exists():
            value = json.loads(path.read_text(encoding="utf-8"))
            if state.get(key) != value:
                state[key] = value
                changed = True
    return changed


def _compact(root, state, data_dir):
    result = context.prepare(root)
    result.pop("root", None)  # Projection paths are transient, never storage locations.
    cached = state.get("brief")
    if cached is not None and not (isinstance(cached, dict) and cached.get("invalidated") is True):
        try:
            _source_paths(cached)
        except ValueError as error:
            result["brief"] = {"status": "invalid", "reasons": [str(error)]}
    result["storage"] = {"database": str(Path(data_dir) / "trurl.sqlite3"), "scope": "active Hermes profile"}
    return result


def context_tool(args, state, data_dir):
    _arguments(args, {"action", "kind", "ids", "offset", "limit"})
    action = args.get("action", "prepare")
    with _projection(state, data_dir) as root:
        if action == "prepare":
            if set(args) - {"action"}:
                raise ValueError("Context preparation accepts no record filters")
            result = _compact(root, state, data_dir)
            return result, _capture(root, state, ("profile",))
        if action != "records":
            raise ValueError("Choose context action prepare or records")
        ids, offset, limit = args.get("ids", []), args.get("offset", 0), args.get("limit", 10)
        if (not isinstance(ids, list) or any(not isinstance(value, str) or not value for value in ids)
                or len(ids) > 20 or len(set(ids)) != len(ids)
                or type(offset) is not int or type(limit) is not int):
            raise ValueError("Use distinct text IDs and integer offset/limit")
        return context.records(root, args.get("kind"), ids, offset, limit), False


def sync_graph(state, data_dir, bindings=None):
    with _projection(state, data_dir) as root:
        result = graph_anchors.sync_anchors(root, bindings)
        return result, _capture(root, state, ("graph", "graph_anchors"))


def maintenance(state, data_dir, plan=None, event="broad", check=False):
    with _projection(state, data_dir) as root:
        operation = hypotheses.maintenance_check if check else hypotheses.maintenance_plan
        return operation(root, plan, event)


def graph_check(state, data_dir, candidates, plan_id):
    with _projection(state, data_dir) as root:
        return graph_updates.check_update(root, candidates, plan_id)


def graph_tool(args, state, data_dir):
    _arguments(args, {"action", "plan_id", "candidates", "review", "delta", "reason", "bindings",
                      "kind", "ids", "offset", "limit"})
    action = args.get("action", "sync")
    if action == "records":
        _arguments(args, {"action", "kind", "ids", "offset", "limit"})
        kind, ids, offset, limit = args.get("kind", "nodes"), args.get("ids", []), args.get("offset", 0), args.get("limit", 10)
        if (kind not in {"sources", "nodes", "edges"} or not isinstance(ids, list)
                or any(not isinstance(value, str) or not value for value in ids) or len(ids) > 20
                or len(set(ids)) != len(ids) or type(offset) is not int or offset < 0
                or type(limit) is not int or not 1 <= limit <= 20 or ids and offset):
            raise ValueError("Use a graph section and distinct IDs or a bounded page")
        current = state.get("graph", graph.empty_graph())
        graph.validate_graph(current)
        values = current[kind]
        if ids:
            selected = [next((item for item in values if item["id"] == key), None) for key in ids]
            if any(item is None for item in selected):
                raise ValueError("Unknown graph record ID")
            next_offset = None
        else:
            selected = values[offset:offset + limit]
            next_offset = offset + len(selected) if offset + len(selected) < len(values) else None
        return {"kind": kind, "total": len(values), "items": copy.deepcopy(selected), "next_offset": next_offset}, False
    if action == "sync":
        if set(args) - {"action", "bindings"}:
            raise ValueError("Graph synchronization accepts only explicit anchor bindings")
        return sync_graph(state, data_dir, args.get("bindings"))
    if action not in {"plan", "check", "complete"}:
        raise ValueError("Choose graph action sync, records, plan, check or complete")
    _arguments(args, {"action", "plan_id", "candidates", "review", "delta", "reason"} if action == "complete"
               else {"action", "plan_id", "candidates"})
    plan_id = args.get("plan_id")
    if not isinstance(plan_id, str) or plan_id not in state.get("plans", {}):
        raise ValueError("Graph review requires a current pending plan_id")
    pending = state["plans"][plan_id]
    if not pending.get("discovery"):
        raise ValueError("Complete hypothesis maintenance and resume this plan before research")
    candidates = args.get("candidates")
    if (not isinstance(candidates, list) or len(candidates) > 96 or not all(isinstance(item, dict) for item in candidates)
            or len(json.dumps(candidates, ensure_ascii=False).encode()) > 256_000):
        raise ValueError("Use at most 96 candidate objects within 256 kB")
    with _projection(state, data_dir) as root:
        if action == "plan":
            sync = graph_anchors.sync_anchors(root)
            result = graph_updates.update_plan(root, candidates, plan_id)
            if sync["status"] != "current" or sync["warnings"]:
                result["graph_anchors"] = sync
        elif action == "check":
            return graph_updates.check_update(root, candidates, plan_id), False
        else:
            review = args.get("review")
            if not isinstance(review, dict) or review.get("batch_id") != plan_id:
                raise ValueError("Graph completion requires this plan's review")
            result = graph_updates.complete_update(root, candidates, review, args.get("delta"), args.get("reason", ""))
        changed = _capture(root, state, ("graph", "graph_anchors", "graph_maintenance"))
        return result, changed


def reconcile(state, data_dir):
    """Reconcile managed taste with current evidence without authoring inferences."""
    profile = state["profile"]
    if not profile.get("taste_hypotheses") and not any(
            isinstance(item, dict) and (item.get("hypothesis_id") or str(item.get("id", "")).startswith("hypothesis:"))
            for item in profile.get("interests", [])):
        return False
    with _projection(state, data_dir) as root:
        hypotheses.refresh(root)
        reviewed = _read_profile(root)
    changed = reviewed != profile
    if changed:
        state["profile"] = reviewed
    return changed


def brief_info(state, data_dir):
    """Never return stale summary prose, even when an old cache remains in state."""
    cached = state.get("brief")
    if cached is not None and not (isinstance(cached, dict) and cached.get("invalidated") is True):
        try:
            _source_paths(cached)
        except ValueError as error:
            return {"status": "invalid", "reasons": [str(error)]}
    with _projection(state, data_dir) as root:
        result = brief.check(root)
        info = {"status": result["status"], "reasons": result["reasons"]}
        if result["status"] == "fresh":
            info.update(summary=result["brief"]["summary"], version=result["version"],
                        file_sha256=hashlib.sha256((root / brief.BRIEF_PATH).read_bytes()).hexdigest())
    return info


def brief_tool(args, state, data_dir):
    _arguments(args, {"action", "draft", "reason"})
    action = args.get("action", "check")
    if action == "check":
        return brief_info(state, data_dir), False
    if action not in {"snapshot", "save", "invalidate"}:
        raise ValueError("Choose brief action check, snapshot, save or invalidate")
    with _projection(state, data_dir) as root:
        if action == "snapshot":
            snapshot = brief.snapshot(root, extra_sources=("model/recommendations.jsonl",))
            return {"snapshot": snapshot, "context": _compact(root, state, data_dir),
                    "limits": list(EVIDENCE_LIMITS)}, False
        if action == "save":
            draft = args.get("draft")
            _source_paths(draft)
            result = brief.save(root, draft)
        else:
            result = brief.invalidate(root, reason=args.get("reason", "manual"))
        path = root / brief.BRIEF_PATH
        cached = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
        changed = cached != state.get("brief")
        if changed:
            state["brief"] = cached
        response = {key: value for key, value in result.items() if key != "brief"}
        if action == "save":
            response["file_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        return response, changed


def hypothesis_tool(args, state, data_dir):
    _arguments(args, {"action", "draft", "plan_id", "event", "review", "outcome", "reason", "hypothesis_ids"})
    action = args.get("action", "snapshot")
    if action in {"maintenance-plan", "maintenance-check", "maintenance-complete"}:
        _arguments(args, {"action", "plan_id", "event", "review", "outcome", "reason", "hypothesis_ids"}
                   if action == "maintenance-complete" else {"action", "plan_id", "event"})
        plan_id = args.get("plan_id")
        pending = state.get("plans", {}).get(plan_id) if isinstance(plan_id, str) else None
        if plan_id is not None and pending is None:
            raise ValueError("Unknown pending plan_id")
        plan = pending["plan"] if pending else None
        event = args.get("event", "broad")
        with _projection(state, data_dir) as root:
            if action != "maintenance-complete":
                operation = hypotheses.maintenance_plan if action == "maintenance-plan" else hypotheses.maintenance_check
                return operation(root, plan, event), False
            review = args.get("review")
            if (not isinstance(review, dict) or (plan is not None and
                    (review.get("request_scope") != plan.get("scope") or review.get("request_mode") != plan.get("mode")))
                    or "event" in args and review.get("event") != event):
                raise ValueError("Complete the review for this retained plan")
            outcome, reason = args.get("outcome"), args.get("reason")
            if not isinstance(outcome, str) or not isinstance(reason, str):
                raise ValueError("Maintenance completion requires text outcome and reason")
            result = hypotheses.complete_maintenance(root, review, outcome, reason, args.get("hypothesis_ids"))
            return result, _capture(root, state, ("hypothesis_maintenance",))
    if action not in {"snapshot", "save", "refresh"}:
        raise ValueError("Choose a supported hypothesis or maintenance action")
    _arguments(args, {"action", "draft"} if action == "save" else {"action"})
    with _projection(state, data_dir) as root:
        if action == "snapshot":
            return {"snapshot": hypotheses.snapshot(root), "context": _compact(root, state, data_dir),
                    "limits": list(EVIDENCE_LIMITS)}, False
        if action == "save":
            draft = args.get("draft")
            _source_paths(draft)
            result = hypotheses.save(root, draft)
        else:
            result = hypotheses.refresh(root)
        reviewed = _read_profile(root)
        changed = reviewed != state["profile"]
        if changed:
            state["profile"] = reviewed
        return result, changed


def atlas_tool(args, state, data_dir):
    _arguments(args, {"action"})
    if args.get("action", "render") != "render":
        raise ValueError("Choose atlas action render")
    target = Path(data_dir).resolve() / "taste-atlas.html"
    with _projection(state, data_dir) as root:
        result = atlas.export_atlas(root)
        changed = _capture(root, state, ("graph", "graph_anchors"))
        os.replace(result["output"], target)
    return {**result, "output": str(target)}, changed
