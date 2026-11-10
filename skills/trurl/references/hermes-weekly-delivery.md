# Sunday delivery in Hermes

On-demand editions work independently. Existing delivery choices stay unchanged; installing/updating the plugin or requesting an issue is not enrollment.

During genuine new-profile setup, call `trurl_schedule(action="notice")` and present its proposed Sunday edition at 20:00 local time. Explain that each edition runs as an ordinary request on the person's plan, using their current preferences, and that “one-off only” skips weekly delivery. Give the person a chance to opt out in their next setup reply. If everything was supplied at once, give a useful selection and the notice first. A reply solely about memory or past chats does not continue schedule enrollment.

After disclosed setup continues without opting out, or an explicit scheduling request, inspect native jobs. Preserve paused jobs until resumption is requested. Use Hermes's scheduler to create/resume the matching task, then `trurl_schedule(action="confirm")` with the real job ID, timezone and authorization evidence. The plugin verifies an active job; `weekly_enabled=true` is rejected. Ask for timezone only when unavailable, and distinguish proposed from user-confirmed time.

Use `status` to inspect delivery, `decline` to skip the offer and `off` to close the plugin's weekly gate. When stopping, pause/remove the matching native job too and report whether Hermes confirmed it. Missing or unverified scheduling leaves delivery inactive. The plugin creates no job or external messaging destination on its own.

Every scheduled run checks current settings before research. Stop when weekly delivery is off, even if an old automation runs. Otherwise verify the schedule and use current categories, constraints, compact context and ordinary completion workflow. Use `context="weekly"` only from that verified schedule with the local Sunday date. A chat request uses `on_demand`.

Deliver one verified work per chosen category or report gaps. Use committed SVG/Markdown files and log only delivered picks. Hypothesis maintenance, checked graph updates and duplicate checks still apply.
