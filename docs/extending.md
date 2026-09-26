# Extending Iris

Recipes for the changes people actually make. Each one lists the files, the test
that will fail if you get it wrong, and how to verify. Read
[`docs/architecture.md`](architecture.md) first if you want the map; this page
assumes you know roughly where things live.

**The house rule:** every extension lands with a test that would fail if the
behaviour were removed, and nothing is relaxed to get green. If a change makes an
existing test awkward, that test is either wrong (fix it and say so in the
commit) or the change is.

## Verify your work

```bash
uv run ruff check .                                            # lint (a gate, not a suggestion)
uv run pytest tests -q --ignore=tests/test_memory_pipeline.py   # fast, deterministic, no DB
uv run pytest tests -q                                         # everything, needs the pgvector DB
docker compose up -d postgres                                  # the database the line above wants
uv run iris doctor                                             # environment sanity, names only
uv run iris --help                                             # the surface you just changed
```

The suite is split by what it needs: everything except `tests/test_memory_pipeline.py`
runs with no database and no network. Five tests need a real pgvector service,
and they **fail loudly** rather than skipping — see [`docs/support.md`](support.md).

---

## Add a tool

A tool is four things: an implementation, a **class declaration**, a schema, and
a test. The class is what derives its policy — `allow` / `ask` / `deny` — so it
is not optional metadata.

1. **Implement it** next to the others in `src/iris/agent/tools.py` (or in the
   module that owns the capability, as `iris/computer/` does).
2. **Declare its class** in `src/iris/toolpolicy.py::TOOL_DECLARATIONS`:

   ```python
   TOOL_DECLARATIONS["send_postcard"] = ToolDeclaration(
       name="send_postcard", klass=ToolClass.DELIVERY, surface="extended",
   )
   ```

   | Class | Default policy | Use for |
   |---|---|---|
   | `read` | `allow` | memory search, traces, stats |
   | `filesystem` | `allow` | sandboxed file tools |
   | `memory_write` | `ask` | `remember`, `forget`, `note` |
   | `network` | `ask` | `web_search`, `ingest_url` |
   | `credentialed` | `ask` | anything using an API key |
   | `delivery` | `ask` | `send_message`, `send_photo` |
   | `control` | `ask` | `computer` |

3. **Add its schema** to `tool_schemas()` in `agent/tools.py`.
4. **Run the coverage test** — `tests/test_tool_policy.py` fails in both
   directions if you forgot the declaration or declared a tool that does not exist.

Read this before choosing `allow`: the class default applies to everything in it,
and **`deny` beats every override**, including a class-wide deny that a per-tool
`allow` tries to re-open. `iris tools` shows the resolved policy and where it came
from.

**Refusals are not errors.** Return a JSON string with `ok: false` and a reason
the model can act on ("lane='escalate' searches daily notes instead"), because a
tool that answers only "no" teaches the model to retry.

---

## Add a skill

Skills are procedural memory. Two encodings are supported and both are discovered
automatically: the flat sidecar pair Iris writes herself
(`workspace/skills/<name>.json` + `.md`) and the open **Agent Skills** layout
(`<name>/SKILL.md`, <https://agentskills.io/specification>) that shipped builtins
use.

```bash
mkdir -p skills/my-skill/scripts
cat > skills/my-skill/SKILL.md <<'MD'
---
name: my-skill
description: One sentence on when to use this.
license: MIT
metadata:
  iris-triggers: "phrase one, phrase two"
allowed-tools: "memory_search, file_read"
---
# My skill

1. Step one.
2. Step two.
MD
uv run iris skills validate     # exits 1 on errors
uv run iris skills show my-skill
```

| Rule | Why |
|---|---|
| `allowed-tools` **narrows** the turn while the skill is active | a skill can never re-open what the session closed |
| an unknown tool in `allowed-tools` is a validation **error** | a manifest that lies about the surface is worse than a missing one |
| script code runs only through `skill_run` | one gate, one audit trail, one place to reason about |
| `scripts/` gets a JEV safety judgment before it runs, and the owner approves it | see `docs/jev.md` §3 #4 |

Scripts run in a constructed environment (no `.env`, no provider keys) as the same
OS user. That is **process** isolation, not kernel isolation — read the residual
risk note in [`docs/deployment.md`](deployment.md) before shipping someone else's
code.

---

## Add a client (a new channel)

A channel is anything that can feed Iris a message and show the reply. Do not add
a second turn pipeline: implement the contract in
`src/iris/channels/brain.py::BrainClient` (`respond`, `resume`, `stream`,
`json_get`, `json_post`) or reuse `HttpBrainClient` and point it at the core.

- `mcp_servers/telegram/` is the worked example over HTTP.
- `src/iris/cli/chat.py` is the worked example in-process.

**Idempotency is the channel's job.** Telegram redelivers updates; the bridge
keeps `UpdateLedger` for exactly that. If your transport can deliver a message
twice, deduplicate before you call `respond`, or the owner's memory gets the same
fact twice.

The auth header comes from `iris.security.auth_headers(token)` — one definition,
shared with the server-side check, so a client and a server cannot disagree about
the scheme.

---

## Add a role (a specialist)

Roles are declared, not prompt-pasted. Add one in `src/iris/agents/roles.py` with
its bounds explicit: tier, recall lane, tool allowlist, round cap, output cap.
Then:

- `iris agents roles` shows it with the bounds that shape it;
- `Orchestrator` enforces the caps in code — an over-long answer is truncated, a
  run past its deadline is cut, and the delegation is recorded as a **typed
  `Handoff`** with provenance (which claims are sourced, and which are not);
- the fan-out decision itself is a JEV judgment (`iris/jev/agents.py`), not a
  regex, so a new role inherits it.

Keep the read-only default: a research role gets `READ_ONLY_TOOLS` and cannot
write memory. Widening that is a deliberate change with a test.

---

## Add a guard

A guard is a function that can only ever **refuse**, and it runs before dispatch:

```
budget → circuit → spiral/dedup → context → record
```

1. Add the state to `src/iris/guards.py` (see `SpiralDetector` for the shape: a
   `note()` that returns a `Verdict`, and a `reset()` that clears turn-scoped
   state only).
2. Call it from `GuardChain.before()` **in that order** and return
   `Verdict(False, GuardName.YOURS, reason, detail)`.
3. Give the reason a next step. The chain's whole point is that a refusal is
   actionable.
4. Record it — `GuardChain.record()` already writes every refusal to the turn
   trace, and `iris guards` / `GET /guards` read the snapshot.

Its tests belong next to the others in `tests/test_guards.py` (unit) and
`tests/test_guard_wiring.py` (a refused call **never reaches `dispatch`** — assert
that, not that a message was printed).

Everything in the chain must be deterministic and model-free. A guard that needs
a model to decide whether to spend money can itself run away.

---

## Add an eval metric or a judgment

**A metric for the lab** (`scripts/eval_lab.py`): add it to the per-query
outcomes and report it through `iris.eval.stats` — a rate gets `wilson_interval`,
a mean gets `bootstrap_mean_ci`, and a comparison gets `paired_difference_ci`, so
query difficulty cancels. Then register it in the pre-registered `DecisionRule`
before you run it. A point estimate with no interval is not a measurement, and a
rule chosen after seeing the numbers is how every ablation "wins".

**A judgment with JEV** — the question to ask yourself is *"is this a decision
about supplied text?"*. If it is, JEV should make it (see `docs/jev.md` §3.0 for
the audit of every model call). Follow the shape of
`src/iris/jev/recall.py`: build one `state` plus typed questions, send **one
batched request**, fall back to the existing deterministic path when `ask()`
returns `None`, and record the probabilities in the turn trace.

If the answer is generation — a summary, a header, a rewritten reply — it stays
with the LLM, and it should say so in the docstring the way
`iris/jev/client.py` does.

---

## Change a setting

1. Add the field to `src/iris/config.py` with a comment explaining *why* it
   exists, not what it is.
2. Add it to `.env.example` (a test fails if you forget — every setting must be
   documented, and every key there must name a real setting).
3. Add it to the relevant section of [`docs/deployment.md`](deployment.md) if an
   operator has to make a decision about it.
4. Prefer a default that preserves today's behaviour, and `0`/empty meaning
   "no limit" rather than "limit of zero".

---

## House conventions

- **Comments explain why.** "Sort by score" is noise; "sorting by score keeps the
  cheap tier from outranking a source the owner pinned" is the reason the next
  person needs.
- **Docstrings state the contract and the degradation.** Every optional layer says
  what happens when it is absent.
- **`from __future__ import annotations`**, `slots=True` dataclasses, `StrEnum`
  for closed vocabularies, and no new dependency without a reason in the commit
  message.
- **Errors are values where the caller can act**, exceptions where it cannot.
- **Never print a secret value.** `iris doctor` prints names and `set`/`missing`.
- **Tests are named for the invariant**, not the function: `test_deny_beats_a_class_allow`,
  not `test_resolve_2`.
