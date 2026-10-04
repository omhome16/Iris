# Phase 0 — truth pass

1. Scope the Landlock assertion in `test_component_check_blocks_ctypes_dns_and_utime` to Linux. Elsewhere assert `isolation=audit`.
2. Folder kinds are context, memory, persona, capture, consolidator. `iris new <other>` prints the entry-point recipe. `scaffold("channel")` raises.
3. `list_options("channel")` is the channel registry. `terminal` and `http` are not in it.
4. `write_new` does not write `[components] channel`.
5. Capture gates (owner, `capture_enabled`, daily cap) run before a custom capture component.
6. `REQUIRED` includes `nearest` and `list_chunks`. The JSON memory example and both scaffolds implement them.
7. `Reindexer` copies `[N]` into `ChunkRecord.importance`.
8. `HookBus.emit_policy` turns a raising handler into `Verdict(allowed=False, guard="hook:<name>")`. `_tools` uses it. `emit` stays fail-open.
9. `attach` records `runtime.harness_identity`. Traces add `harness`, `policy_class`, `decision`, `context_chars`, `tokens`, `cost_usd`, `model`.
10. `/explain`, `/trace`, and `iris trace` render that record.
