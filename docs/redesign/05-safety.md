# 05 — Safety, permissions & sandboxing

Iris is open source and will run other people's tools and other people's servers.
Safety must be structural: a capability that is not declared cannot run, a denial
cannot be re-opened by a lower layer, and a secret never reaches a trace.

## 1. Capability classes

Every tool, from core or MCP, declares a class. The class resolves to a default
policy, and a user override can only ever *tighten*:

| Class | Default | Examples |
|---|---|---|
| `read` | allow | memory search, traces, stats, MCP read tools |
| `filesystem` | allow (sandboxed) | file read/write inside the sandbox |
| `memory_write` | allow | `remember`, `note`, `forget` |
| `network` | allow, output screened | `web_search`, `ingest_url`, remote MCP |
| `delivery` | allow | `send_message`, `send_photo` |
| `credentialed` | ask | anything using an API key |
| `exec` | ask | skill scripts, shell, MCP stdio servers |
| `control` | ask | computer-use, destructive actions |

**Deny beats every override; a tool not declared is not registered.** (Both
already true in `toolpolicy.py`; the redesign keeps them and extends the classes
to MCP servers.)

## 2. Policy resolution

```
class default  <  per-server default (MCP)  <  per-tool override  <  session grant (tightening only)
```

`deny` short-circuits. `ask` raises a durable approval. `allow` proceeds but is
still traced. Resolution is pure data, readable with `iris policy`.

## 3. Approvals

Kept from P8 and generalized:

- an approval is bound to a **digest of the effective arguments** after edits, so
  an edited resume cannot pass as the original;
- one `tool_call_id` grants **once per thread**;
- a side-effecting action with no digest **fails closed**;
- the approval is a **journal event**, so it survives a restart (`01-kernel.md`);
- the prompt shows what the owner is approving and never a secret value.

## 4. Sandboxing

Three levels, chosen per capability:

| Level | Mechanism | Default for |
|---|---|---|
| in-process | path jail (traversal/absolute rejected) | file tools |
| process | no-env subprocess, timeout, capped output, `HOME` jailed | skill scripts, MCP stdio |
| container | rootless container, no network unless granted | `exec` when available |

Process isolation is the default floor; container isolation is opt-in because it
requires Docker, and principle 1 says Docker must not be required to start.
`docs/deployment.md`'s residual-risk note carries over.

## 5. Untrusted content

The provenance model extends to sources: web results, ingested pages, MCP output
from `review`/`untrusted` servers, and files are screened by the judgment layer
for instruction injection and **structurally tagged** (`[UNTRUSTED …]`) so the
model sees data-as-data. Untrusted content is recallable but never promotable to
curated memory.

## 6. Secrets

- Only an allowlisted env reaches a subprocess; provider keys are passed to the
  model client explicitly, never exported to tools.
- The trace records tool arguments as a hash, never verbatim; a redaction pass
  strips known secret shapes; the cost ledger holds no secrets.
- OAuth tokens live in the OS keychain, not in the manifest or journal.
- `iris doctor` prints names and `set`/`missing`, never values.

## 7. Kill switch

A global switch refuses every turn before it spends (no model call, no tool
dispatch), recorded as a refusal. It is config and CLI reachable.

## 8. Threat model (explicit)

| Threat | Mitigation |
|---|---|
| Prompt injection from a page/MCP server | screen + structural tag + untrusted-never-promoted |
| A model looping/costing money | guard chain + budgets, pre-dispatch |
| A malicious skill script | judgment gate + approval + stripped env + timeout |
| A compromised remote server | per-server trust, namespacing, deny-by-default for `untrusted` |
| Secret leakage into logs/trace | arg hashing + redaction + keychain |
| Destructive action without consent | class→ask + digest-bound approval, fail-closed |
