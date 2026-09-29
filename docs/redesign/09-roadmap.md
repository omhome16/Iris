# 09 — Roadmap & migration

Phased so the project is **never left broken**. Each phase is independently
valuable, ships behind tests, and ends with a working harness. The existing
suite (910 tests, ruff clean) is the safety net; every phase must keep it green
or consciously relocate a test with a recorded reason.

The already-shipped plug-and-play layer (registry, hooks, channel registry, tool
provider, manifest, `iris plugins`) is **Phase 0**.

## Phase 0 — Plug-and-play foundation ✅ (shipped)

Registries for channels/tools, the hook bus with the guard chain as first
subscriber, a manifest, and the capability CLI. This is the seam everything else
plugs into.

## Phase 1 — Backends become swappable (low risk, high clarity)

**Goal:** no capability is reached through a concrete class; each is a Protocol.

- `MemoryBackend` interface + an in-memory backend; pgvector adapter behind it.
  *(touches `memory/index.py`, `null_index.py`, `engine.py`)*
- `ModelBackend` interface; `LiteLLMBackend` adapter. *(touches `memory/llm.py`)*
- `Judge` interface; `JevJudge` + deterministic adapters. *(touches `jev/`)*
- Conformance tests per interface; message-contract test enforced.
- Migration: **0 user-visible change**; same behavior, new seams.

**Gate:** suite green; a test constructs each concrete backend through its
interface and asserts the contract.

## Phase 2 — SQLite default + one-command start (onboarding)

**Goal:** clone → `iris init` → `iris chat` in under five minutes with no service.

- `capabilities/memory/sqlite_vec.py` as the default backend; embeddings with a
  no-key fallback (FTS-only); `iris migrate` from pgvector.
- `iris init` wizard; `iris.toml` written; a no-op model check proves the setup.
- Docker Compose becomes optional, not the documented happy path.
- **(blast: memory backends, config, CLI, docs, docker)** — the riskiest phase for
  onboarding claims, so it ends with a CI job that runs the five-minute path on a
  clean checkout.

**Gate:** a CI job times the clean-checkout path (<5 min) and asserts a reply.

**Status: shipped.**

- `memory/sqlite_index.py` — one file, no daemon: FTS5 keyword recall, plus
  **brute-force exact cosine** when an embedding provider exists. No native
  extension, deliberately: the default path has no build step. With no key it
  degrades to keyword-only and says so, in the log and in `iris init`'s report.
- `iris init` is the one command, and it **measures** rather than asserts: one
  no-op completion, one open of the store, one walk of the real checkpointer
  ladder. `iris migrate` rebuilds a pgvector index from the Markdown.
- Threads keep their own tier ladder (`postgres` → `sqlite` → in-memory with a
  warning), which is a *separate* choice from where recall lives — tying them
  meant a SQLite memory user silently lost durable threads.

**The gate, as tested:** CI job `onboarding` runs `uv sync --frozen` →
`iris init --offline` → `pytest tests/test_harness.py -k no_database` on a clean
checkout with **no Postgres service in the job**, and fails past 300 s. The
harness side is `tests/test_harness.py` (`::test_a_clone_with_no_database_boots_full`,
`…_persists_threads`, `…_can_still_recall`); the index has 28 tests in
`tests/test_sqlite_index.py`; the two commands have `tests/test_onboarding.py`.

**Deliberately absent:** `capabilities/memory/sqlite_vec.py` as written — the
file is `memory/sqlite_index.py` and uses no `sqlite-vec`. The extension is
installed as a transitive dependency, but the default backend does not call it:
an exact scan over a personal corpus is fast enough, and a default that needs a
compiled extension is a default that fails on someone's laptop.

**Goal:** add any MCP server from config; per-server trust and approval.

- `McpClient` + transports (`stdio`, `http`/`streamable-http`, `sse`, `ws`);
  `.mcp.json` import; tool namespacing `server/tool`; health/reconnect.
- Per-server `trust`/`approval`; tool classes extended to MCP.
- **(blast: `channels/`, `toolregistry.py`, `toolpolicy.py`, `engine.py`)**

**Gate:** a test boots stdio + HTTP servers, namespaces their tools, and proves an
`untrusted` server's write tool is denied by default.

**Status: shipped.** Declaration (`.mcp.json`, `${VAR}`), the trust model
(`policy_for`), namespacing, the client (`open_server`; stdio, http, sse), the
pool that wires it into the turn, background reconnect, and the
`iris plugins mcp [--live]` readout. **51 tests** (4 more in the CLI readout)
(`tests/test_mcp_servers.py`, `tests/test_mcp_provider.py`, plus the CLI
readout in `tests/test_plugins_cli.py`); the handshake runs over the SDK's
in-process transport, the only one that works in every event loop.

**The gate, as tested:** `tests/test_mcp_provider.py::
test_a_connected_servers_tools_reach_the_model_and_a_denied_one_never_does` boots
the real harness against an in-process server and asserts an untrusted server's
write tool is absent from the visible schemas **and** from the deferred catalog
(so `find_tools` cannot reach it either), while a read-only tool is offered and
callable. `test_a_denied_external_tool_is_refused_even_if_it_is_called` proves the
second, independent barrier at `dispatch`.

Two decisions the phase settled, both recorded in the code:

1. **`toolpolicy` gained a dynamic declaration path** (`declare_external` +
   `ExternalTool` + `ToolClass.EXTERNAL`), rather than an entry in
   `TOOL_DECLARATIONS`. The core table is asserted to cover core tools in *both*
   directions — that is what makes "every core tool is classified" checkable — so
   an external name in it would weaken the one claim it supports.
2. **stdio is refused under the CLI's event loop on Windows**, with the reason,
   rather than attempted: the transport needs asyncio subprocesses and the
   selector loop (which `iris chat`/`iris api` select because psycopg needs it)
   does not implement them. `skills/runner.py` already documents the same wall.
   `McpUnsupported` marks that refusal as *permanent*, so the reconnect loop does
   not retry a fact that cannot change.

Not done, stated rather than implied: a server's **output** reaches the model
unscreened (Phase 4's injection screening is where that changes), and `ws://` is
refused because this SDK ships no websocket client transport — refused rather than
silently downgraded to another transport.

## Phase 4 — Safety hardening

**Goal:** the threat model in `05` is enforced, not described.

- Sandbox levels (in-process / process / container-optional); secret store;
  OAuth 2.1 token store in the keychain.
- Policy engine generalized to servers; `iris policy` readout.
- Injection screening applied to every `review`/`untrusted` MCP source.
- **(blast: `sandbox.py`, `security.py`, `approval.py`, new `secrets/`)**

**Gate:** adversarial tests (malicious server output, malicious skill script,
edited resume) all fail closed.

**Status: shipped, with one deliberate gap named below.**

- **Screening follows the trust level.** `trust` is `owner` (`trusted` accepted)
  | `review` | `untrusted`, and the last two are the screened levels: the pool
  runs the injection guard over every reply, **withholds** it on a block
  (`ok: false`, `withheld: true`, no `text`) and tags every reply as untrusted
  data. No judge available means tagged + `screened: false`, never "checked".
- **`iris policy`** — classes, overrides (and what each one really applies to,
  plus the typos), and every declared server previewed from the declaration.
- **Secret store** (`iris_ai.secrets`) with `env` / `keyring` / `file` backends,
  `auto` choosing up front, `${VAR}` resolving env-then-store in a real load, and
  `iris secrets backend|list|set|rm` reporting **names and locations only**.
- **`EXEC_SANDBOX=process|container`** for a skill's script: the container level
  adds the host-level guarantees (no network, read-only root, memory/PID caps,
  non-root, `:ro` mount) and **fails closed** without a runtime. No `in_process`
  level, on purpose.

**The gate, as tested:** `tests/test_mcp_provider.py`
`::test_a_blocking_screen_withholds_the_text_entirely` (malicious server output),
`tests/test_skill_runner.py::test_guard_blocks_a_script_it_judges_unsafe` plus
`::test_the_container_level_fails_closed_without_a_runtime` (malicious script),
and `tests/test_approval_wiring.py::test_the_digest_pins_the_arguments_the_action_actually_uses`
(edited resume — closed by construction, since a resume carries a decision and no
arguments, and the digest is recomputable from the payload the owner saw).

**Not done, and why:** the OAuth 2.1 authorization flow (a loopback callback and a
browser) — the *token store* half now exists, and `02` already defers the
registry half to v2 because discovery without a trust story is how a hostile
server gets connected. Static bearer headers are the supported remote path today.

**Also tightened beyond the phase list:** a session that cannot ask (a scheduled
task, a cron heartbeat) is now refused *before* a tool whose policy is `ask` can
reach an interrupt nobody can answer — pausing forever is not a refusal. That
landed with Phase 3's wiring and is tested in `test_mcp_provider.py`.

## Phase 5 — Kernel + durability

**Goal:** the explicit turn state machine and the journal; LangGraph optional.

- `kernel/turn.py`, `kernel/journal.py`, replay, idempotent tool boundaries,
  durable approvals, versioned prompts/tools.
- `Orchestrator` modes (`react` / `plan-execute` / `delegate`); LangGraph adapter.
- **(blast: `agent/chat.py`, `agents/orchestrator.py`, `budget.py`, `guards.py`) —
  the largest change; done only after the interfaces (P1) abstract it.**

**Gate:** kill-mid-tool recovery test (exactly-once); replay-divergence test;
full suite green.

**Status: shipped as the boundary, with the orchestrator swap deferred.**

- `kernel/journal.py` — an append-only turn journal (JSONL, one file per thread)
  that records what a turn ran with (`prompt_version`, an order-independent
  `tools` digest) and every settled step, so a settled call **replays** instead of
  running again.
- `kernel/turn.py` — the boundary decision: a step that may have run is one of
  three cases, and they are not the same case. `settled` replays; a
  **side-effecting** step with no end is *refused* rather than repeated (an
  unknown outcome is not a licence to do it twice); a **read-only** one simply
  runs; and an **interrupt** re-enters, because a paused turn is not a crash.
- Durable approvals: a spent grant survives the process, so a restarted harness
  cannot be talked into a second ``forget`` by replaying the resume.

**The gate, as tested:** `tests/test_kernel_journal.py` (16 tests) —
`::test_a_side_effecting_step_that_may_have_run_is_refused_not_repeated` and
`::test_a_read_only_step_that_may_have_run_simply_runs` are the kill-mid-tool
pair; `::test_a_settled_call_replays_instead_of_running_again` and
`::test_replaying_twice_yields_the_same_replies_and_runs_nothing` are the replay
divergence pair; `::test_a_spent_approval_survives_the_process` is durability;
`::test_a_kernel_without_a_journal_still_works` pins the degradation.

**Not done, stated rather than implied:** the `Orchestrator` modes
(`react` / `plan-execute` / `delegate`) and the non-LangGraph runtime. LangGraph
remains the orchestrator in v1; the kernel is the boundary it calls into. See
`README.md` §9 Q3 for the decision and `docs/redesign/01-kernel.md` for what the
adapter still owes.

## Phase 6 — Interfaces & interoperability

**Goal:** Iris is usable from an editor and observable by a backend.

- ACP adapter; OTel GenAI exporter (optional); `iris mcp` verbs; `iris costs`.
- MCP-server mode deferred to v2 (see `08` §5).
- **(blast: new `interfaces/acp/`, `trace.py`)**

**Gate:** an ACP session drives a turn end-to-end in a test; OTel spans validate
against the semantic conventions' schema.

**Status: shipped.**

- **ACP** (`src/iris_ai/interfaces/acp/`, entry point `iris-acp`) — sessions map
  to threads by `acp:<id>`, tool calls stream as `session/update`s, an approval
  interrupt becomes `session/request_permission` answered through the same
  `ApprovalGate` the CLI uses, unsupported prompt content is refused rather than
  dropped, and `session/cancel` answers `stopReason: cancelled`. The four mapping
  decisions are written down in [`docs/acp.md`](../acp.md).
- **OTel GenAI spans** (`src/iris_ai/observability/`) — `turn` and `<tool>` spans
  shaped to the conventions, exported only when asked, through an optional
  `[otel]` extra. Asking for `otlp` without the extra is a boot error naming it.
- **`iris costs`** and **`iris mcp`** — the ledger's terminal reader (totals, by
  model, daily, weekly; an unpriced model is *named*, never counted at zero) and
  the verbs that write `.mcp.json` without hand-editing JSON.

**The gate, as tested:** `tests/test_acp_adapter.py` (12 tests) drives turns,
tool calls, approvals (asked, approved, refused) and cancellation through the
adapter, with `::test_a_prompt_survives_a_real_jsonrpc_round_trip` framing a whole
session with the SDK's own `ClientSideConnection` over an in-memory transport.
Spans are checked in `tests/test_otel_spans.py` (17 tests) by
`::test_every_span_stays_on_convention`, which asserts every attribute is one the
project declares as convention-defined — there is no schema file to load, so the
check is the one that can actually fail. `tests/test_costs_mcp_cli.py` (15)
covers the two CLI surfaces.

A real bug surfaced here and was fixed in the same phase: the **streamed**
approval path read the interrupt out of the checkpoint's `values`, where
LangGraph no longer mirrors it, so a turn that paused for approval looked to a
streaming client like a turn that simply ended. Both the CLI and the adapter read
it through `chat._interrupt_value` now, and the ACP approval tests are what pin
it — a second reason for an interface to exist is that it exercises the path
nobody else did.

**Deliberately absent:** `session/set_mode` (there are no modes; the router says
`method not found`), streaming the *resumed* half of an interrupted turn (the
kernel returns a final reply; its tool calls are journalled, not streamed), and
client-supplied MCP servers (reported and not adopted — nobody in the process
vouched for them).

## Phase 7 — Reference app + release discipline

**Goal:** the personal assistant ships as `examples/assistant/`; the harness is
published.

- Move the assistant config/persona/skills under `examples/`; the default profile
  is neutral.
- Docs: a five-minute quickstart, a plugin-authoring guide, a compatibility
  matrix. Versioning + release workflow. Security scanning in CI.

**Gate:** a fresh clone runs the example and the neutral default; the wheel
installs and `iris --help` works from the artifact.

**Status: shipped.**

- **The assistant moved to `examples/assistant/`** — a persona (`AGENTS.md`), a
  workspace README, and a `harness.toml` that points `workspace_dir`, the two
  index files and the Telegram channel at the example. `HARNESS_CONFIG=… iris
  init` is the whole setup, and the section above the manifest explains what it
  demonstrates and what it must *not* grow.
- **The default profile is neutral.** `src/iris_ai/templates/AGENTS.md` and
  `…/WORKSPACE-README.md` are what the harness ships: no persona, no channel, no
  life. `iris init` seeds them into a workspace that has none (and never
  overwrites one that does — not even with `--force`, which covers `.env` and the
  manifest), so an installed wheel begins with a contract rather than with memory
  and no instructions. `tests/test_packaging.py` asserts the packaged copy and the
  repo's tracked `workspace/` copy are **byte-identical**, and that the neutral
  contract claims no persona the reference app owns.
- **Docs:** [`docs/quickstart.md`](../quickstart.md) (the timed five-minute path,
  with its failure modes), [`docs/plugins.md`](../plugins.md) (install or write a
  plugin: the entry-point groups, the Protocols, a worked tool + hook plugin, the
  rules discovery enforces), [`docs/acp.md`](../acp.md),
  [`docs/observability.md`](../observability.md), and the compatibility matrix in
  [`docs/support.md`](../support.md#compatibility-matrix--the-optional-surfaces).
- **Release discipline:** the tag-driven `release.yml` (TestPyPI before PyPI,
  trusted publishing rather than a token), and a `security` CI job.

**The gate, as tested:** the wheel's console entry and packaged data are checked
in CI's `package` job and by `tests/test_packaging.py`; the neutral default and
the example are covered by
`::test_the_default_profile_templates_are_the_tracked_workspace_files` and
`::test_the_neutral_default_does_not_claim_a_persona`;
`iris init`'s seeding is covered by `tests/test_onboarding.py`
(`::test_init_seeds_a_neutral_workspace_the_first_time`,
`::test_init_never_overwrites_the_workspace_the_owner_edited`).

**Found while doing it:** the dependency audit added in this phase found a real
vulnerability the redesign had inherited — `httpx2` 2.10.0 (three advisories),
reached transitively through the MCP SDK. It is upgraded to 2.13.1 in the lock.
That is the `security` job earning its place on its first run.

**Deliberately not done:** `iris.toml` is still `harness.toml` (D3's name never
mattered to a user, and the existing file is documented everywhere), and the
repository layout is *not* rearranged into `kernel/ capabilities/ interfaces/` in
full — the new packages landed where they belong, while the working modules
(`agent/`, `memory/`) kept their paths so the redesign did not become a rename
with a diff nobody could review.

## Cross-cutting rules

- **No phase leaves `main` broken.** Each is a branch that keeps the suite green.
- **A number in the docs names the method that produced it.**
- **Every "add" has a conformance test; every "remove" has a recorded reason.**
- **The reference app never grows features the harness cannot express** — if it
  needs one, that is a missing capability interface.

## Migration map (current → redesign)

| Current | Becomes |
|---|---|
| `agent/chat.py::ChatGraph` | `kernel/turn.py` + `orchestrators/langgraph.py` *(deferred: Phase 5 shipped the boundary, and LangGraph still drives the graph — see §9 Q3 in `README.md`)* |
| `memory/llm.py` | `capabilities/models/litellm.py` |
| `memory/index.py` (pgvector) | `capabilities/memory/pgvector.py` (+ `sqlite_vec.py`) |
| `memory/{files,dreaming,forgetting}.py` | `capabilities/memory/` (called by kernel) |
| `jev/*` | `capabilities/judges/jev.py` |
| `agent/tools.py` | `capabilities/tools/builtin/` |
| `channels/telegram_mcp.py` | `capabilities/channels/telegram.py` (via MCP client) |
| `agents/orchestrator.py` | `orchestrators/delegate.py` |
| `eval/*` | `capabilities/eval/` |
| `cli/*`, `api.py` | `interfaces/{cli,api}/` |
| `harness()` / `engine.py` | `kernel/boot.py` (assembly from manifest) |

## Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| Kernel rewrite regresses subtle behavior | High | interfaces first (P1), kill/replay tests (P5), keep LangGraph adapter |
| SQLite default underperforms on large corpora | Medium | pgvector stays a one-line backend switch |
| MCP breadth explodes scope | Medium | v1 manual specs only; registry browse deferred |
| Safety changes break UX | Medium | default profile keeps current permissive-for-owner behavior; tightening only |
| Docs drift from code | Medium | numbers name their method; CI checks examples run |
| The 910-test suite encodes old structure | High | relocate tests with the code, record counts in CHANGELOG, never delete to pass |

## Open questions blocking finalization

See `README.md` §9 — package identity, Python-only confirmation, LangGraph
adapter timing, MCP-server-mode priority, and confirming single-user stays in
scope.
