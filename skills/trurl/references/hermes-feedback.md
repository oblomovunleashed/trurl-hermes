# Scoped feedback in Hermes

Use `trurl_feedback` for actual reactions and corrections. Preserve words in `note`, an accurate target and scope. Default `item` feedback needs a delivered `recommendation_id`; topic, creator, presentation, global and current-request feedback need their real target and words. Never invent a delivered item for a broader correction.

When the original source is identifiable, supply `source_provenance` as one to eight source IDs or objects with an `id`, `locator` or `message_id` (and `thread_id` when available). Reuse the same identity for extracts from one message. Without it, reactions share an unlocated-conversation source, so two saved events alone cannot establish independent corroboration. The separate `event_locator` identifies the saved SQLite record; it is not an original user-message source. Identical reactions from distinct identified messages remain separate, while retries with the same source identity remain idempotent.

“Already knew this,” “too introductory,” “would try,” “completed” and “liked” carry different evidence. Delivery, silence, clicks and completion do not prove enjoyment. An item reaction changes that item first; broader genre, creator or expertise claims need supporting wording or independent evidence. Concentration complaints can change attention allocation without establishing dislike of the dominant field.

Add a lasting interest only when explicitly requested, such as `more_like_this` with an `interest`. Keep `current_request` corrections outside the enduring model. Let the latest explicit correction govern within its scope and preserve contradictions. Repeated summaries of one source are not independent corroboration.

Hypothesis support must refer to the actual delivered work and retained hypothesis. Broader `confirmed_interest` or `rejected_interest` requires `hypothesis_id` and actual user words. One enjoyed work leaves the connection tentative. Reconcile, inspect due maintenance and explicitly complete it with `trurl_hypotheses`; do not manufacture a proposal.

Existing version-2 records that used generated SQLite event locations as source evidence migrate to the shared unlocated source while retaining their words, dates, targets and event IDs. Dependent hypotheses become stale and their managed interests leave active discovery until reviewed again. Existing identifiable original sources are preserved.

Refresh or invalidate affected briefs through `trurl_brief`. For forgetting, remove requested raw evidence and dependent interpretations/exports within the authorized scope; invalidation alone does not erase sources. If native tools cannot remove the requested raw record, report that limit and use an explicitly authorized storage-maintenance procedure; never claim deletion merely from invalidation. Read [shared evidence rules](state-contract.md) for provenance details, using native tools rather than standalone filesystem commands.

Learn from volunteered feedback. Ask for it only in a workflow the person requested or enabled; ordinary pick delivery ends after the picks.
