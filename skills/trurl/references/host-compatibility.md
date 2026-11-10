> Hermes adapter: use this plugin's native `trurl_*` tools and its storage bridge. SQLite in the active Hermes profile remains authoritative; the bridge creates and removes file snapshots internally. Any `model/` paths below describe internal evidence formats, not files to create or edit. The native calls below replace standalone commands; pass arguments to the registered tools rather than invoking a shell. Retain one plan and its returned seeds. Use Hermes's verified native scheduler for delivery.

# Hermes capabilities

Load the bundled skill as `trurl:trurl` and use the registered `trurl_*` tools. The active Hermes profile owns persistent SQLite state; `trurl_context(action="prepare")` reports its location. Supporting references are local to this skill. No standalone root or host installer is needed.

- **Research:** use Hermes’s available research tools and direct sources. Report unavailable verification.
- **Memory and chats:** use only separately authorized sources actually exposed by Hermes. Permission does not imply availability; retain provenance and gaps.
- **Scheduling:** the host creates/pauses native jobs and `trurl_schedule` verifies activation. If scheduling is unavailable, keep delivery inactive and offer an on-demand edition.

Respect source restrictions, unknown knowledge, learned weights and existing delivery choices. A newly available capability supplies no preference evidence.

## Understand the test boundary

Installation checks can establish that a complete, private-state-free folder is placed in a documented location and that its helpers work outside the repository. Native host checks must additionally observe that the host discovers/loads the installed skill and executes its helpers. Authenticated model runs require the host's own credentials and tool permissions. Do not label a missing CLI, missing login, or packaging-only check as a passing complete host run.

Skill discovery and helper integration do not establish better recommendations. Evaluate the research, attribution, feedback, and scheduling behavior separately using the repository's behavioral scenarios before making broader quality claims.
