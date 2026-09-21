# The console

The dashboard is the argument. Iris's claim is *"memory you can see and trust"*,
and a trust layer you have to open a panel to inspect is a trust layer you are
taking on faith — so the console is one canvas with nine live panels and no
collapsed sidebar.

![the console at dawn](screenshots/console-dawn.png)

## Design language

**A dawn sky, drawn in three drifting strata.** The background is a vertical
gradient (zenith blue → peach → cream) with three tiled layers of blurred blobs
moving at different speeds and opacities. The parallax is the whole trick: one
gradient reads as a stock background, three layers at 300 s / 190 s / 120 s read
as weather. The night theme is the same drawing after dark (navy → violet,
clouds at 8–14 % opacity, a cold moonlit glow instead of a warm sun).

Rules the sky obeys:

- **Decoration, never information.** Every layer is `aria-hidden`, and no number
  is ever drawn on the sky — data lives on frosted panels
  (`backdrop-filter: blur(20px)` over a translucent fill).
- **It stops when asked.** `prefers-reduced-motion: reduce` freezes all drift.
- **It stays readable.** Cloud layers are pointer-events-none and sit behind
  every panel.
- **`prefers-contrast: more`** swaps the frosted panels for opaque ones and drops
  the blur entirely.

Typography is three voices: **Fraunces** (a variable serif) for the brand and
panel titles — the "artistic" register — **IBM Plex Sans** for prose, and
**IBM Plex Mono** for anything she measured. The mono/serif split is
load-bearing: if it is monospaced, it came from a file, a ledger or a model.

## The panels

| Panel | Source | Why it is on the canvas |
|---|---|---|
| **masthead vitals** | `/api/health` | core reachability, chunks by origin, **judgment-layer state**, today's spend, background work in flight |
| **conversation** | `/api/chat/stream` (SSE) | the turn itself, with thinking and tool calls streaming live |
| **judgment** | latest entry of `/api/traces` | the last turn's decisions — see below |
| **memory** | `/api/mind` | `MEMORY.md` · `USER.md` · `AGENTS.md` · `DREAMS.md`, plus provenance counts from the index |
| **dream diary** | `/api/mind` | `DREAMS.md` and the unverified-claim count |
| **forgetting** | `/api/retention`, `/api/rot` | retention curve (one mote per stored chunk, hover for path/age/retention) and what is fading |
| **skills** | `/api/skills` | the procedures she wrote for herself |
| **schedule** | `/api/tasks` | reminders she is holding for you |
| **spend** | `/api/costs` | per-day cost, tokens, cache-hit rate |
| **turn history** | `/api/traces` | recent turns with per-stage latency and what each one captured |

### The judgment panel

This is the panel that did not exist before, and the reason to read a trace:

- **recall ranking** — for each top hit, the probability JEV assigned it, the
  blended relevance, and the final `score`. The caption states the composition
  rule out loud: `score = probability × recency decay × importance`. Relevance
  is judged; **forgetting policy stays in code**, because "what may Iris forget"
  is not a judgment to outsource.
- **untrusted content** — every screened item with its verdict
  (`pass` / `review` / `block`) and its injection/harm scores. Items that were
  **not** screened show as `unscreened` with a reason; "not checked" and "checked
  and clean" must never look alike.
- **skill selection** — the chosen skill, or `none`, with the two numbers the
  gate used (`needs_skill`, `confidence`).
- **write path** — capture's verdict (`captured [importance]` or `nothing kept`)
  **with its reason**, since "declined" and "never ran" are different facts about
  a mind; plus whether reflection ran inline, in the background, or was skipped.
- **where the time went** — a waterfall of `assemble`, `agent`, `tools`,
  `rerank`, `guard`, `capture`, `reflection`, `jev` in milliseconds, which is how
  the claim "reflection is off the reply path" is checkable rather than asserted.

## Accessibility

Verified, not assumed — the first pass of this design failed its own audit:

- **Contrast.** axe reports the colour-contrast rule as *incomplete* on this
  page, because the panels are translucent over a gradient and it cannot compute
  the backdrop. So contrast is measured independently: for each text style, the
  real ancestor background chain is composited (translucent panels included) over
  **both extremes of the sky gradient**, and the worst ratio is kept. The first
  measurement caught the dawn theme's faint tier at **2.8–3.4:1**, below WCAG AA;
  the token was darkened until 22 text styles pass in both themes. Two further
  lessons from that exercise: custom properties resolve to token text, not
  `rgb()`, and Chrome reports `color-mix()` as `color(srgb …)` with 0–1
  components — a naive `rgb()` parser silently mis-reads both.
- **Zero axe violations** at desktop (1440 px) and phone (420 px) widths. Fixed
  along the way: scrollable regions now take `tabindex` (`scrollable-region-focusable`),
  the spend figures are a real `<dl>` instead of loose `<dt>/<dd>`, and the
  retention chart keeps its `<title>`/`<desc>` on redraw — the first version
  counted `childNodes` (whitespace included) and deleted its own accessible name.
- **No layout overflow** at 420 px; the board collapses 12 → 6 → 1 column.
- **No server string reaches `innerHTML`.** Every value goes through
  `textContent`, so a memory containing markup, or a hostile ingested page,
  renders as text.
- Keyboard: every scrollable panel is focusable, `:focus-visible` outlines are
  visible, `<summary>` toggles the live activity log, tabs are real `role="tab"`
  buttons with `aria-selected`.

## Previewing it without a database

The console talks only to iris-core over HTTP, so it can be rendered against
canned payloads — useful for design work and for review, since it needs neither
Postgres nor a provider key. Serve `dashboard/templates/index.html` plus
`dashboard/static/` and stub the `/api/*` routes (`/api/health`, `/api/mind`,
`/api/retention`, `/api/rot`, `/api/skills`, `/api/tasks`, `/api/costs`,
`/api/traces`, `/api/jev`). The screenshots above were captured this way, at
1440 px and 420 px, in both themes, with `prefers-color-scheme: dark` honoured
by default and the choice persisted in `localStorage`.

## Theme

`dawn` is the light theme, `night` the dark one. The toggle sits in the masthead,
the OS preference is respected on first visit, and the choice is remembered.
Both themes are built from the same token set in `dashboard/static/style.css` —
adding a third (say, dusk) means adding one `[data-theme]` block, nothing else.
