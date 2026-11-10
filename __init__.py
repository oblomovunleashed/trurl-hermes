"""Register Trurl's tools and on-demand workflow with Hermes."""

from pathlib import Path
import json

from .engine.scripts.diversity import CONNECTION_BASES
from .workflow import CATEGORY_IDS, REACTIONS, invoke

TEXT = {"type": "string"}
BOOL = {"type": "boolean"}
OBJECT = {"type": "object"}
FEEDBACK_SOURCES = {
    "type": "array", "minItems": 1, "maxItems": 8,
    "description": "Identifiable original user messages or profile source IDs, not stored feedback event locations.",
    "items": {"oneOf": [
        {"type": "string", "minLength": 1, "maxLength": 500},
        {"type": "object", "additionalProperties": False,
         "properties": {key: {"type": "string", "minLength": 1, "maxLength": maximum}
                        for key, maximum in {"id": 500, "kind": 80, "locator": 2000, "message_id": 500,
                                             "thread_id": 500, "captured_at": 80, "recorded_at": 80}.items()},
         "anyOf": [{"required": [key]} for key in ("id", "message_id", "locator")]},
    ]},
}
SOURCE_CHOICE = {"type": "object", "additionalProperties": False, "properties": {
    "permission": {"type": "string", "enum": ["allowed", "declined", "not_asked"]},
    "scope": TEXT,
    "availability": {"type": "string", "enum": ["available", "partial", "unavailable", "unknown"]},
}}
CATEGORIES = {"type": "array", "minItems": 1, "uniqueItems": True,
              "items": {"type": "string", "enum": list(CATEGORY_IDS)}}
SCORES = {axis: {"type": ["number", "null"], "minimum": 0, "maximum": 5} for axis in (
    "depth", "novelty", "relevance", "surprise", "perspective_change", "entertainment")}
CANDIDATE_FIELDS = {
    "id": TEXT, "title": TEXT, "creator": TEXT, "canonical_url": TEXT,
    "medium": {"type": "string", "enum": list(CATEGORY_IDS)},
    "lane": {"type": "string", "enum": ["familiar", "adjacent", "distant", "focused"]},
    "topics": {"type": "array", "items": TEXT},
    "scores": {"type": "object", "properties": SCORES, "required": list(SCORES)},
    "credibility_passed": BOOL, "constraints_passed": BOOL,
    "verification": {"type": "object", "required": ["checked_at", "urls"],
                     "properties": {"checked_at": TEXT, "urls": {"type": "array", "items": TEXT}}},
    "description": TEXT, "detail": TEXT,
    "personal_connection": {"type": "object", "required": ["basis", "anchor", "explanation"],
                            "properties": {"basis": {"type": "string", "enum": sorted(CONNECTION_BASES)},
                                           "anchor": TEXT, "explanation": TEXT}},
    "personal_fit_status": TEXT, "value_rationale": TEXT,
    "hypothesis_id": TEXT, "source_family": TEXT, "discovery_domain": TEXT,
}
REQUIRED_CANDIDATE_FIELDS = [
    "id", "title", "creator", "canonical_url", "medium", "lane", "topics",
    "scores", "credibility_passed", "constraints_passed", "verification",
]
TOOL_DEFINITIONS = {
    "trurl_context": (
        "Prepare compact current taste context, or fetch original records selectively. Checks brief freshness and reconciles hypotheses; preserves the full interest index without returning whole ledgers.",
        {"action": {"type": "string", "enum": ["prepare", "records"]},
         "kind": {"type": "string", "enum": ["interest", "hypothesis", "provenance", "feedback", "recommendation", "batch"]},
         "ids": {"type": "array", "maxItems": 20, "items": TEXT},
         "offset": {"type": "integer", "minimum": 0},
         "limit": {"type": "integer", "minimum": 1, "maximum": 20}}, [],
    ),
    "trurl_taste": (
        "Inspect or edit the lasting profile. Add supplied interests; replace/remove only on explicit correction. Imported memory and chats require separate scoped permission. Use trurl_context for compact reads.",
        {"interests": {"type": "array", "maxItems": 200, "items": TEXT}, "categories": CATEGORIES,
         "replace_interests": BOOL, "remove_interest_ids": {"type": "array", "items": TEXT},
         "brain_dump": TEXT, "source": {"type": "string", "enum": ["current_conversation", "agent_memory", "past_chats"]},
         "source_note": TEXT, "context_sources": {"type": "object", "additionalProperties": False,
                                                   "properties": {"agent_memory": SOURCE_CHOICE, "past_chats": SOURCE_CHOICE}},
         "user_words": TEXT,
         "preferences": {"type": "object", "additionalProperties": False, "properties": {
             "constraints": {"type": "array", "items": TEXT, "maxItems": 30}, "presentation": TEXT,
             "axes_weights": {"type": "object", "properties": {axis: {"type": "number", "minimum": 0} for axis in SCORES},
                              "required": list(SCORES), "additionalProperties": False},
             "variation": {"type": "string", "enum": ["adaptive", "fixed"]},
             "open_exploration": BOOL, "open_exploration_scope": TEXT,
             "graph_max_hops": {"type": "integer", "minimum": 1, "maximum": 3}}},
         "weekly_enabled": {"type": "boolean", "description": "Legacy opt-out only; true is rejected. Use trurl_schedule to confirm a native job."}}, [],
    ),
    "trurl_plan": (
        "Create or resume one retained plan before research. Supply request initially; resume with plan_id after a due hypothesis review. Preserves weights and seeds. Weekly runs require a verified native schedule.",
        {"request": TEXT, "plan_id": TEXT, "categories": CATEGORIES, "count": {"type": "integer", "minimum": 1, "maximum": 12},
         "mode": {"type": "string", "enum": ["auto", "personal", "surprise", "deep", "playful", "perspective", "open", "wildcard"]},
         "scope": {"type": "string", "enum": ["auto", "focused", "broad"]},
         "context": {"type": "string", "enum": ["on_demand", "weekly"]}, "edition_date": TEXT, "fixed": BOOL},
        [],
    ),
    "trurl_graph": (
        "Synchronize interest representation or review sourced content relationships after research. Plan/check/complete bind the exact plan_id and candidate pool. Complete with reviewed delta or an explicit no-change reason before selection.",
        {"action": {"type": "string", "enum": ["sync", "records", "plan", "check", "complete"]},
         "plan_id": TEXT, "candidates": {"type": "array", "maxItems": 96,
              "items": OBJECT, "description": "The complete candidate records intended for trurl_select, unchanged."},
         "bindings": {"type": "array", "maxItems": 200, "items": {
             "type": "object", "properties": {"anchor_id": TEXT, "node_id": TEXT},
             "required": ["anchor_id", "node_id"], "additionalProperties": False}},
         "kind": {"type": "string", "enum": ["sources", "nodes", "edges"]},
         "ids": {"type": "array", "maxItems": 20, "items": TEXT},
         "offset": {"type": "integer", "minimum": 0},
         "limit": {"type": "integer", "minimum": 1, "maximum": 20},
         "review": OBJECT, "delta": OBJECT, "reason": {"type": "string", "maxLength": 500}},
        ["action"],
    ),
    "trurl_select": (
        "Preview or commit researched picks under one plan. Requires current hypothesis/graph completion and the exact reviewed pool. Commit only delivered picks; retries are idempotent. Dated editions produce linked Markdown/SVG.",
        {"plan_id": TEXT, "commit": BOOL, "candidates": {"type": "array", "maxItems": 96,
          "items": {"type": "object", "properties": CANDIDATE_FIELDS,
                    "required": REQUIRED_CANDIDATE_FIELDS}}},
        ["plan_id", "candidates"],
    ),
    "trurl_feedback": (
        "Record explicit reactions with their actual scope and words. Item feedback needs a delivered pick ID; broader hypothesis confirmation needs its ID. Request-only corrections never change lasting taste.",
        {"recommendation_id": TEXT, "reaction": {"type": "string", "enum": list(REACTIONS)}, "note": TEXT,
         "interest": TEXT, "hypothesis_id": TEXT, "target": TEXT,
         "source_provenance": FEEDBACK_SOURCES,
         "scope": {"type": "string", "enum": ["item", "topic", "creator", "presentation", "global", "current_request"]}},
        ["reaction"],
    ),
    "trurl_brief": (
        "Check, snapshot, save or invalidate the evidence-bound brief. Snapshot before drafting; add summary (350 words maximum), source_refs and limits. Fetch evidence selectively with trurl_context. Stale prose is suppressed.",
        {"action": {"type": "string", "enum": ["check", "snapshot", "save", "invalidate"]}, "draft": OBJECT,
         "reason": {"type": "string", "enum": ["manual", "source_corrected", "source_forgotten"]}}, [],
    ),
    "trurl_hypotheses": (
        "Propose/reconcile tentative interests or complete triggered maintenance. Preserve original anchors and scoped evidence. A due review needs explicit completion, including a reasoned no_proposal when appropriate.",
        {"action": {"type": "string", "enum": ["snapshot", "save", "refresh", "maintenance-plan", "maintenance-check", "maintenance-complete"]},
         "draft": OBJECT, "plan_id": TEXT,
         "event": {"type": "string", "enum": ["broad", "onboarding", "feedback"]},
         "review": OBJECT, "outcome": {"type": "string", "enum": ["saved", "reviewed", "no_proposal"]},
         "reason": {"type": "string", "maxLength": 500},
         "hypothesis_ids": {"type": "array", "items": TEXT}}, [],
    ),
    "trurl_atlas": (
        "On request, export the private interactive Taste Atlas with sourced content paths, recommendation reasons and tentative hypotheses. Synchronizes interest representation without inventing relationships or preferences.",
        {}, [],
    ),
    "trurl_schedule": (
        "Disclose Sunday setup or inspect/confirm/disable delivery. Only a verified active native Hermes job activates weekly delivery. Preserve existing choices; off immediately closes the weekly gate.",
        {"action": {"type": "string", "enum": ["notice", "status", "confirm", "off", "decline"]},
         "timezone": TEXT, "time": TEXT, "job_id": TEXT, "user_words": TEXT, "notice_id": TEXT,
         "authorization": {"type": "string", "enum": ["explicit_request", "onboarding_continued"]}}, [],
    ),
}


def _handler(ctx, name):
    def handle(args, **kwargs):
        def scheduler():
            dispatch = getattr(ctx, "dispatch_tool", None)
            if dispatch is None:
                return {"success": False, "error": "Native Hermes scheduler is unavailable"}
            result = dispatch("cronjob_manage", {"action": "list", "include_disabled": True})
            return json.loads(result) if isinstance(result, str) else result
        return invoke(ctx.state.data_dir, name, args, scheduler=scheduler)
    return handle


def register(ctx):
    for name, (description, properties, required) in TOOL_DEFINITIONS.items():
        ctx.register_tool(
            name=name, toolset="trurl", description=description,
            schema={"name": name, "description": description, "parameters": {
                "type": "object", "properties": properties, "required": required,
                "additionalProperties": False,
            }},
            handler=_handler(ctx, name),
        )
    ctx.register_skill(
        "trurl", Path(__file__).resolve().parent / "skills" / "trurl" / "SKILL.md",
        description="Personal recommendations, a persistent taste model, cross-theme discovery, Sunday editions and the Taste Atlas.",
    )
