> Hermes adapter: use this plugin's native `trurl_*` tools and its storage bridge. SQLite in the active Hermes profile remains authoritative; the bridge creates and removes file snapshots internally. Any `model/` paths below describe internal evidence formats, not files to create or edit. The native calls below replace standalone commands; pass arguments to the registered tools rather than invoking a shell. Retain one plan and its returned seeds. Use Hermes's verified native scheduler for delivery.

# Sunday edition

The Sunday edition is one verified pick in each chosen format, presented as a designed issue with a linked text version. Request an issue any time. On hosts with scheduling, first-time profile setup includes Sunday delivery unless the person opts out after seeing the proposed slot. Use the [weekly delivery rules](weekly-delivery.md) to check activation or turn delivery off.

## Select the issue

1. Use the categories in the request, or the profile’s enabled categories if the request leaves them open. Keep the chosen order. Trurl’s six standard categories are books, podcasts, films, YouTube videos, lectures and scientific papers. Assign each work to one category in the candidate pool. A lecture hosted on YouTube can fill either relevant category, with its canonical work appearing once across the edition.
2. Check the curator’s brief, the current profile, past recommendations and feedback as usual. Make one [request plan](adaptive-weights.md) before research. Use the `weekly` planning context for an active scheduled run and `on_demand` for a requested issue; `trurl_plan(request=..., context="weekly", edition_date=...)` makes the plan and stops a scheduled run while delivery is disabled. Apply medium, time, language and other explicit limits within each category. Check [hypothesis maintenance](taste-hypotheses.md#check-and-complete-maintenance) against that plan and complete any due review before research; an unchanged current completion record can be reused.
3. Research more than one lead per category where the budget allows. Verify the work, creator, direct link and any details printed in the issue. Score each qualifying candidate under the retained plan. Complete the [graph update](knowledge-graph.md#save-verified-relationships-after-research) for this candidate pool and batch ID, saving supported relationships or a reasoned no-change outcome. Pass the chosen media once as the request plan’s ordered `categories` list. It selects at most one eligible work per category, applies the lane limits across the whole issue, and reports unfilled categories in `missing_categories`.
4. Follow the [skill's delivery rules](../SKILL.md#verify-select-and-present-the-works). Give each work a one-line description and include useful verified details such as a running time or publication year.

Use `trurl_select(plan_id=..., candidates=...)`.


## Render and deliver

Set the local Sunday `edition_date` and ordered `categories` when creating `trurl_plan`; the issue has one slot per category. Preview the reviewed pool with `trurl_select`. Include public-facing `description` and useful verified `detail` fields on each candidate.

Use `trurl_select(plan_id=..., candidates=..., commit=true)` only for picks being delivered. The same transaction records the delivered picks and retained plan; retries are idempotent. For a dated issue, its returned edition paths point to the designed `sunday-edition.svg` and linked `sunday-edition.md`. Deliver both, including any reported category gaps. No separate renderer, input file or recording command is required. Hermes supplies research, verification and any active schedule.

For a public example, share only approved picks, descriptions and links. Keep the profile, brief, history and scoring trace private.
