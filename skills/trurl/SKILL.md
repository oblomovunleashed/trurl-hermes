---
name: trurl
description: Curate personal books, films, podcasts, videos, lectures and papers with Hermes-native taste memory, cross-theme discovery, Sunday editions and the private Taste Atlas.
license: MIT
---

# Trurl for Hermes

Use the native `trurl_*` tools. The active Hermes profile's SQLite database owns taste, history, feedback, content graph and completion records. Reuse that storage across sessions. Shared references explain evidence semantics; their standalone CLI/file examples are conceptual here, not another storage layout to create.

## Start with compact context

Call `trurl_context` with `action="prepare"`. It reconciles hypotheses, returns every interest, current settings and a checked fresh brief, with ledger counts instead of raw history. Missing/stale prose is suppressed. Retrieve original evidence with `action="records"`, `kind` and `ids`, or bounded `offset`/`limit` pages. Inspect originals before interpreting selected anchors or feedback. Use `trurl_taste` for requested full-profile inspection and explicit edits.

For a new profile, read [onboarding](references/hermes-onboarding.md). Accept one category and anchor or an unrestricted brain dump. Offer relevant agent memory separately from past-chat review; preserve existing choices and distinguish permission from actual availability. Report the tool's persistent storage location. Updating the plugin grants no source access or weekly enrollment.

Explicit interests, inferred observations, tentative hypotheses, familiarity, consumption and enjoyment remain distinct. Knowledge defaults to unknown. Suggestions, graph paths, clicks, completion and silence establish no taste. Preserve provenance, scoped corrections and quiet interests; infer no sensitive identity traits. Temporary constraints and effective weights belong to the request, preserving the learned baseline.

## Plan before researching

1. Call `trurl_plan` with the request, categories/count and resolved mode/scope. Retain its `plan_id`, weights and seeds. Weekly requests first require a verified active native schedule.
2. When hypothesis review is due, discovery pauses. Use `trurl_hypotheses` with `action="maintenance-plan"` and that `plan_id`; inspect original anchors and competing explanations using [hypothesis rules](references/taste-hypotheses.md). Save at most one defensible proposal, or review existing possibilities. Then explicitly `maintenance-complete` with the returned `review`, outcome, reason and relevant hypothesis IDs. A reasoned `no_proposal` is valid. Resume `trurl_plan` using the same `plan_id`; never redraw to favor a candidate. Onboarding and substantive feedback also surface maintenance triggers.
3. Use the retained discovery briefs to broaden approaches and sources. Connected surprise needs a concrete personal bridge; independent open exploration needs an explicit request or scoped standing permission. Read [discovery details](references/diverse-discovery.md) when needed. Honor medium, duration, language, exclusions and familiarity; credibility and constraints are gates. Pure entertainment needs no educational justification.

Keep the brief current with `trurl_brief`: snapshot before drafting, fetch relevant originals, then save the exact snapshot plus an at-most-350-word summary, source references and limits. Use [brief maintenance](references/curators-brief.md) for refreshes/corrections. A changed original evidence base invalidates an existing research plan; follow the tool's fresh-plan instruction.

## Verify, select and present the works

Research more candidates than the final slate. Verify exact title, creator, direct HTTPS work link, medium and material claims through appropriate original sources. Separate factual confidence, generation likelihood and taste judgments. Preserve uncertainty and report unavailable verification.

After research, call `trurl_graph` with `action="plan"`, the retained `plan_id` and the exact candidate pool. Inspect existing facts through `action="records"`. Complete the returned review with sourced `delta` changes and a reason, or explicitly complete with no supported changes. Read [graph evidence rules](references/knowledge-graph.md) when adding relationships or interpreting routes. Current receipts can be reused. Representation alone adds no factual relationship or preference.

Pass the same pool to `trurl_select`. It requires current hypothesis/graph completion, checks duplicates and gates, and previews shortfalls. Use `commit=true` only for picks being delivered; retries are idempotent and only delivered works enter history. Dated editions produce linked SVG and Markdown.

Lead with linked titles, creators, useful verified details and one plain descriptive sentence. Honor requested presentation. Keep scores, modes, reasons and discarded leads private unless asked. Briefly state material shortfalls; otherwise end after the picks.

## Feedback and Atlas

For volunteered reactions, corrections or forgetting, read [feedback](references/hermes-feedback.md). Keep item and broader hypothesis evidence scoped; a batch complaint does not establish dislike of a field. Ask for feedback only in an enabled/requested workflow.

For “show my taste atlas”, call `trurl_atlas` and open/link its private HTML. Its Connections view separates sourced paths, recorded recommendation reasons and tentative taste hypotheses. Every eligible interest is represented automatically; missing sourced routes remain visible. The Atlas never supplies recommendation evidence.

## Sunday delivery

Read [weekly delivery](references/hermes-weekly-delivery.md) only for genuine new-profile setup, a scheduling request or a scheduled run. Preserve existing choices; the native host creates/pauses jobs and `trurl_schedule` verifies activation. Every scheduled run checks current settings and stops before research or delivery when weekly delivery is off. An on-demand edition requires no schedule.
