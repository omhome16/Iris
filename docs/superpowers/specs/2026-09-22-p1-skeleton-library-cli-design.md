# P1 — Skeleton: library-first rebirth + CLI shell

**Date:** 2026-09-22  
**Status:** Approved (sections 1–3) — awaiting written-spec review  
**Phase:** P1 of D-sequence (P1 skeleton → P2 core brain → P3 Telegram → P4 skills → P5 multi-agent → P6 cron → P7 computer-use → P8 ship)  
**Gate rule:** No P2 work until the user marks P1 complete and verified.

---

## 1. Context and product decisions (locked)

| Decision | Choice |
|---|---|
| Relationship to Iris | **In-place rebirth** of this repo (approach A) |
| Ship shape | **B: library + thin CLI** — `import iris_ai` is the product; CLI is the face |
| Long-term vision | Installable personal agent harness: Telegram, computer-use, skills/plugins, multi-agent, cron, memory/context/compaction |
| Frontend | **Web dashboard deleted** in P1; beautiful CLI replaces it |
| Phase order | A: P1 → P8 as table above |

P1 does **not** change agent behavior, memory, Telegram, or add chat.

---

## 2. Goals, non-goals, Definition of Done

### Goals

1. **Library-first repo:** installable package; import path remains `iris`.
2. **Web frontend gone:** entire `dashboard/` tree deleted; no supported HTML/CSS/JS surface.
3. **CLI shell exists and is presentable:** `iris --help`, `iris version`, `iris doctor` — real commands only (no fake `chat` stub).
4. **Honest test migration:** every test is kept (library), edited (dashboard refs removed), or deleted with a recorded reason — no silent drops.
5. **CI green** on the new layout (`ruff` + `pytest`).
6. **Docs match reality:** README describes library + CLI with later phases called out as future; console/dashboard claims removed.

### Non-goals

- No changes to agent graph, memory algorithms, JEV, or tool behavior (internal structure may stay as-is).
- No Telegram/bridge changes; no computer-use; no public plugins API; no multi-agent API.
- No PyPI publish; no distribution rename (brand = P8).
- No Textual full TUI; no new product features in CLI beyond section 3.
- No “dashboard kept behind a flag.”

### Definition of Done (user verification checklist)

```text
[ ] uv sync && uv run iris --help        → styled help, no traceback
[ ] uv run iris doctor                   → provider/key presence (names only), package path, version
[ ] uv run iris version
[ ] dashboard/ does not exist
[ ] uv run ruff check .                  → clean
[ ] uv run pytest tests -q               → all green (count recorded in CHANGELOG)
[ ] README quickstart matches reality    → clone → sync → iris --help works
```

Exit codes: `iris doctor` returns `1` only if a **fail** check fires; warnings do not fail.

---

## 3. Target repository layout

```text
Iris/
├── src/iris_ai/                 # library package (import path UNCHANGED)
│   ├── __init__.py           # minimal public export list for P1 (see §6)
│   ├── cli/                  # NEW
│   │   ├── __init__.py
│   │   ├── main.py           # typer.Typer root + [project.scripts] entry
│   │   ├── help_theme.py     # rich styling helpers
│   │   ├── doctor.py
│   │   └── version.py
│   ├── config.py
│   ├── agent/  memory/  jev/  channels/  …   # untouched this phase
│   └── …
├── tests/
├── docs/superpowers/specs/   # this document
├── scripts/
├── mcp_servers/              # kept for P3
├── pyproject.toml
├── README.md                 # rewritten in P1
└── CHANGELOG.md
```

### Hard deletes

| Path | Action |
|---|---|
| `dashboard/` | Delete entire tree (app, static, templates, Dockerfile, requirements, pycache) |
| `docs/console.md` | Delete |
| `docs/screenshots/console-*.png` | Delete (including audit screenshots) |
| Root `console-live-*.png` | Delete |
| `dashboard.log`, `dashboard-err.log` | Delete |
| `docker-compose.yml` dashboard service | Remove **service only** — postgres/core remain |

### Keep

- All `src/iris_ai/**` library code (no dashboard imports exist today).
- `mcp_servers/telegram/**` (P3).
- `tests/**` except dashboard-only tests (rules in §5).
- `.github/workflows/ci.yml` — update if it references `dashboard`.

---

## 4. CLI surface (P1 only)

### Entry point

```toml
[project.scripts]
iris = "iris_ai.cli.main:app"
```

### Commands

| Command | Behavior |
|---|---|
| `iris --help` / `iris -h` | Rich-rendered root help: description, commands, global options |
| `iris version` / `iris -V` | Prints `iris <ver>`, `python <ver>`, `package <path>` |
| `iris doctor` | Offline checks (table or aligned list): each `ok` / `warn` / `fail` |

**No** `iris chat` (or other stubs) until the feature exists.

### `iris doctor` checks

| Check | ok | warn | fail |
|---|---|---|---|
| `.env` readable | present | missing → hint `cp .env.example .env` | unreadable |
| Package importable | print location | — | cannot `import iris_ai` |
| ≥1 provider key | name(s) `set` | none → set a provider key | — |
| `TYPESAFE_API_KEY` | set | missing → JEV off, deterministic fallback | — |
| Postgres | not checked in P1 | — | — |

**Never print secret values** — only `set` / `missing` (and optionally last 4 chars if already used elsewhere; default: no suffix).

### Global flags

| Flag | Effect |
|---|---|
| `--version` / `-V` | same as `version` |
| `--debug` | full tracebacks on error |

### UX rules

1. **Rich when TTY**; plain text when piped or `NO_COLOR` set.
2. **Errors:** one-line cause + one actionable next step; traceback only with `--debug` / `IRIS_DEBUG=1`.
3. **No network** in any P1 command (doctor is offline — CI-safe).
4. CLI layer catches exceptions for formatting; library code is not changed to swallow errors.

### Example outputs (normative sketches)

```text
$ iris version
iris 0.1.0
python 3.13.9
package  …/src/iris_ai

$ iris doctor
Iris doctor
  ok    .env present
  ok    package importable
  ok    provider keys: GEMINI_API_KEY, OPENROUTER_API_KEY
  warn  TYPESAFE_API_KEY missing — JEV disabled (deterministic fallback)
2 ok · 1 warn · 0 fail
```

---

## 5. Tests and CI

### Test rules

1. **Delete** dashboard-only tests (e.g. full `tests/test_console_fixes.py`).
2. **Edit** tests that import `dashboard.app` (e.g. `test_security.py`) — remove dashboard proxy cases; **keep** iris-core auth and bridge cases.
3. **Do not** weaken library assertions to get green.
4. Record final test count in CHANGELOG under P1.

### CI

- Keep ruff + pytest workflow.
- Drop any path filters / steps that assume `dashboard/` exists.
- Image build step (if present) must not require dashboard files.

---

## 6. Public package surface (P1)

`src/iris_ai/__init__.py` may export a **minimal** stable list only (e.g. `__version__`). Do not freeze agent internals as public API in P1 — that is P2’s job.

Internal imports inside the monorepo continue to use `iris.*` as today.

---

## 7. Dependencies (P1 delta)

```toml
dependencies = [
  # existing…
  "typer>=0.12",
  "rich>=13",
]
```

No Textual, no new network/db libraries.

---

## 8. Documentation

### README rewrite (outline)

1. One-paragraph pitch: personal agent harness — library + CLI (Telegram, computer-use, multi-agent: **later phases**).
2. Status: **P1** — install + CLI shell; roadmap table P1–P8.
3. Quickstart: `uv sync` → `uv run iris --help` / `doctor`.
4. Architecture: high-level map still true after dashboard removal (drop dashboard box from diagrams).
5. Link to phase specs under `docs/superpowers/specs/`.

### CHANGELOG entry (P1)

- Added: CLI (`version`, `doctor`, help).
- Removed: dashboard, console docs/screenshots, dashboard compose service.
- Changed: package scripts entry; README.
- Tests: dashboard tests removed/edited; final count = **TBD at implementation**.

---

## 9. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Deleting dashboard breaks CI paths | Grep CI + compose + README for `dashboard` before finishing P1 |
| Tests quietly dropped | Spec requires CHANGELOG test count; diff `tests/` in PR |
| CLI becomes fake demo | Forbid command stubs not in DoD list |
| Secrets leak via doctor | Unit test: doctor output never contains env var values |

---

## 10. Out-of-scope reminders

P2 introduces `iris chat` and public `Agent` API design. P3 reconnects Telegram to the library. Nothing in P1 should be designed as if those exist yet.

---

## 11. Approval

- [x] Section 1 (goals / non-goals / DoD) — user: yes  
- [x] Section 2 (layout / deletes / CLI surface) — user: yes  
- [x] Section 3 (UX / errors / deps) — user: yes for all sections  
- [ ] User approved **written** spec (this file)  
- [ ] P1 implemented and user ran DoD checklist  
