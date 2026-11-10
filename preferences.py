"""Apply explicit lasting preferences without inferring baseline changes."""

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json

from .engine.scripts import curation


FIELDS = {
    "constraints", "presentation", "axes_weights", "variation", "open_exploration",
    "open_exploration_scope", "graph_max_hops",
}


def _text(value, name, maximum):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f"{name} must contain 1–{maximum} characters of text")
    return value.strip()


def _validated(args):
    if not isinstance(args, dict):
        raise ValueError("Preference arguments must be an object")
    values = args.get("preferences")
    if not isinstance(values, dict) or not values or not set(values).issubset(FIELDS):
        raise ValueError("preferences must contain supported lasting preference fields")
    words = _text(args.get("user_words"), "user_words", 4000)
    result = deepcopy(values)
    if "constraints" in values:
        constraints = values["constraints"]
        if not isinstance(constraints, list) or len(constraints) > 30:
            raise ValueError("constraints must be the complete array of at most 30 lasting constraints")
        result["constraints"] = [_text(item, "constraint", 500) for item in constraints]
    if "presentation" in values:
        result["presentation"] = _text(values["presentation"], "presentation", 1000)
    if "axes_weights" in values:
        try:
            result["axes_weights"] = curation.normalize_weights(values["axes_weights"])
        except OverflowError as exc:
            raise ValueError("weights must have a finite positive sum") from exc
    if "variation" in values:
        if not isinstance(values["variation"], str) or values["variation"] not in {"adaptive", "fixed"}:
            raise ValueError("variation must be adaptive or fixed")
    if "open_exploration" in values:
        if type(values["open_exploration"]) is not bool:
            raise ValueError("open_exploration must be true or false")
        result["open_exploration_scope"] = _text(values.get("open_exploration_scope"), "open_exploration_scope", 2000)
    elif "open_exploration_scope" in values:
        raise ValueError("open_exploration_scope needs an explicit open_exploration choice")
    if "graph_max_hops" in values:
        hops = values["graph_max_hops"]
        if type(hops) is not int or not 1 <= hops <= 3:
            raise ValueError("graph_max_hops must be an integer from 1 to 3")
    return result, words


def apply_preferences(args, state):
    """Return audited changes; the caller enforces source and updates timestamps.

    Validate the entire request before touching authoritative state. Reapplying
    an already-effective setting is a no-op, rather than another feedback event.
    This operation never derives a preference from a recommendation or score.
    """
    values, words = _validated(args)
    working = deepcopy(state["profile"])
    changes = []

    def change(path, value):
        keys = path.strip("/").split("/")
        node = working
        for key in keys[:-1]:
            node = node.setdefault(key, {})
        before = deepcopy(node.get(keys[-1]))
        if before == value:
            return
        node[keys[-1]] = deepcopy(value)
        changes.append({"path": path, "before": before, "after": deepcopy(value),
                        "reason": "Explicit lasting preference: " + words, "scope": "persistent"})

    if "constraints" in values:
        change("/taste/constraints", values["constraints"])
    if "presentation" in values:
        current = working.get("taste", {}).get("presentation", {})
        desired = {"default": values["presentation"], "kind": "explicit"}
        if {key: value for key, value in current.items() if key != "source_provenance"} != desired:
            change("/taste/presentation", desired)
    if "axes_weights" in values:
        change("/taste/axes_weights", values["axes_weights"])
        change("/taste/weights_status", "explicit_user_preference")
    if "variation" in values:
        change("/taste/weighting_policy/mode", values["variation"])
    if "open_exploration" in values:
        current = working.get("discovery", {}).get("open_exploration", {})
        desired = {"enabled": values["open_exploration"], "scope": values["open_exploration_scope"],
                   "kind": "explicit_user_choice"}
        if {key: value for key, value in current.items() if key != "source_provenance"} != desired:
            change("/discovery/open_exploration", desired)
        change("/discovery/lane_mix", curation.LANES["open" if values["open_exploration"] else "personal"])
    if "graph_max_hops" in values:
        change("/discovery/graph_max_hops", values["graph_max_hops"])
    if not changes:
        return []

    identity = {"kind": "explicit_user_preferences", "preferences": values, "user_words": words,
                "prior": {item["path"]: item["before"] for item in changes}}
    key = hashlib.sha256(json.dumps(identity, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()).hexdigest()
    origin = "preferences:" + key
    for item in changes:
        item["source_provenance"] = [origin]
        if item["path"] in {"/taste/presentation", "/discovery/open_exploration"}:
            item["after"]["source_provenance"] = [origin]
            first, second = item["path"].strip("/").split("/")
            working[first][second] = deepcopy(item["after"])
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    provenance = {"id": origin, "kind": "explicit_user_preferences", "locator": "sqlite:feedback/" + key,
                  "captured_at": now, "source": "current_conversation", "user_words": words}
    working.setdefault("provenance", []).append(provenance)
    event = {"id": key, "recommendation_id": None, "recorded_at": now,
             "signal": "explicit_preferences", "scope": "global", "user_words": words,
             "source_provenance": [origin], "profile_changes": deepcopy(changes)}

    # Keep the caller's profile reference valid; replace no authoritative object
    # until every value and audit record has been validated and constructed.
    state["profile"].clear()
    state["profile"].update(working)
    state.setdefault("feedback", {})[key] = event
    return changes
