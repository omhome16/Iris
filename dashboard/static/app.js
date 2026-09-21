/* Iris console — one canvas, everything on it.
 *
 * Two rules this file follows everywhere:
 *
 * 1. No server string ever reaches innerHTML. Every value goes through
 *    textContent, so a memory that contains markup, a page title, or a
 *    hostile ingested document reads as text instead of executing.
 * 2. Nothing is hidden by default. The judgment panel in particular exists
 *    because a trust layer you cannot inspect is just a claim: each turn shows
 *    which memory won and by how much, what was refused at the door, and how
 *    long each stage took.
 */

const $ = (id) => document.getElementById(id);

const state = {
  session: "web-" + Math.random().toString(36).slice(2, 8),
  busy: false,
  tier: "memory",
  mind: null,
  traces: [],
  jev: null,
};

async function api(path, method = "GET", body = null) {
  const opts = { method, headers: {} };
  if (body) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const r = await fetch(path, opts);
  const data = await r.json().catch(() => ({}));
  if (!r.ok && !data.error) data.error = `HTTP ${r.status}`;
  return data;
}

const el = (tag, cls, text) => {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
};

const pct = (x) => `${Math.round((Number(x) || 0) * 100)}%`;
const ms = (x) => (x >= 1000 ? `${(x / 1000).toFixed(1)}s` : `${Math.round(x || 0)}ms`);
const usd = (x) => `$${(Number(x) || 0).toFixed(2)}`;

/* ── theme ─────────────────────────────────────────────────────────────── */

const THEME_KEY = "iris-sky";

function setTheme(theme) {
  document.documentElement.dataset.theme = theme;
  $("theme-label").textContent = theme === "night" ? "dawn" : "night";
  localStorage.setItem(THEME_KEY, theme);
}

setTheme(
  localStorage.getItem(THEME_KEY) ||
    (window.matchMedia("(prefers-color-scheme: dark)").matches ? "night" : "dawn"),
);

$("theme-toggle").addEventListener("click", () => {
  setTheme(document.documentElement.dataset.theme === "night" ? "dawn" : "night");
});

/* ── vitals ────────────────────────────────────────────────────────────── */

async function refreshVitals() {
  const h = await api("/api/health");
  const stats = h.memory || {};
  const judgment = h.judgment || {};

  const reachable = !h.error;
  $("core-state").textContent = reachable ? "online" : "unreachable";
  $("core-pip").className = "pip " + (reachable ? "is-live" : "");
  $("vital-chunks").textContent = stats.total_chunks ?? "—";

  // The judgment layer's own explanation: live, or why not. "Off" and
  // "silently failing" must never look the same.
  state.jev = judgment;
  const on = Boolean(judgment.enabled);
  $("vital-jev").textContent = on
    ? `live · ${judgment.requests ?? 0} calls`
    : judgment.reason
      ? "off"
      : "—";
  $("vital-jev").title = on ? `model ${judgment.model}` : judgment.reason || "";
  $("jev-pip").className = "pip " + (on ? "is-live" : "is-off");

  const pending = h.background && h.background.pending;
  $("foot-stats").textContent =
    `index ${stats.total_chunks ?? "—"} chunks · ` +
    Object.entries(stats.by_origin || {})
      .map(([k, v]) => `${k} ${v}`)
      .join(" · ") +
    (pending ? ` · ${pending} background` : "");
}

/* ── conversation ──────────────────────────────────────────────────────── */

function addTurn(role, text) {
  const log = $("chat-log");
  const turn = el("div", `turn turn-${role}`);
  turn.append(el("span", "turn-role", role === "user" ? "you" : "iris"));
  turn.append(el("p", "turn-body", text));
  log.append(turn);
  log.scrollTop = log.scrollHeight;
  return turn;
}

$("composer").addEventListener("submit", async (event) => {
  event.preventDefault();
  const input = $("input");
  const text = input.value.trim();
  if (!text || state.busy) return;

  input.value = "";
  addTurn("user", text);
  state.busy = true;
  $("send-btn").disabled = true;
  $("hint").textContent = "iris is thinking…";
  $("activity-body").replaceChildren();
  let reply = "";

  try {
    const res = await fetch("/api/chat/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text, session_id: state.session }),
    });
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let idx;
      while ((idx = buffer.indexOf("\n")) >= 0) {
        const line = buffer.slice(0, idx).trim();
        buffer = buffer.slice(idx + 1);
        if (!line.startsWith("data: ")) continue;
        let ev;
        try {
          ev = JSON.parse(line.slice(6));
        } catch {
          continue;
        }
        if (ev.kind === "text") {
          reply += ev.delta || "";
          $("hint").textContent = "iris is typing…";
        } else if (ev.kind === "reply") {
          reply = ev.text || reply;
        } else if (ev.kind === "thinking" && ev.delta) {
          const row = el("div", "act-thinking", ev.delta);
          $("activity-body").append(row);
        } else if (ev.kind === "tool_call" && ev.call) {
          const row = el("div", "act-tool", `⚙ ${ev.call.name || "tool"} ${JSON.stringify(ev.call.args || {})}`);
          $("activity-body").append(row);
        }
        $("activity-body").scrollTop = $("activity-body").scrollHeight;
      }
    }
    addTurn("iris", reply || "(no reply)");
    $("hint").textContent = "";
    await Promise.all([refreshVitals(), refreshPanels()]);
  } catch (err) {
    addTurn("iris", "Could not reach iris-core: " + err.message);
    $("hint").textContent = "";
  }
  state.busy = false;
  $("send-btn").disabled = false;
  input.focus();
});

/* ── the judgment panel ────────────────────────────────────────────────── */

const STAGE_LABELS = {
  assemble: "assemble",
  agent: "agent",
  tools: "tools",
  compact: "compact",
  capture: "capture",
  guard: "guard",
  rerank: "rerank",
  reflection: "reflection",
  jev: "jev",
};

function renderJudgment(trace) {
  const body = $("judgment-body");
  body.replaceChildren();

  if (!trace) {
    body.append(el("p", "empty", "No turn yet. Send a message and this fills with what Iris decided."));
    return;
  }

  const events = trace.events || [];
  const stages = trace.stages_ms || {};
  const of = (kind) => events.filter((e) => e.kind === kind);

  $("judgment-note").textContent = `${trace.ts} · ${ms(trace.latency_ms)} total`;

  // ── which memory won, and how sure the model was ──
  const rerank = of("rerank").pop();
  const rerankGroup = el("div", "jgroup");
  rerankGroup.append(el("p", "jgroup-title", "recall ranking"));
  if (rerank) {
    rerankGroup.append(
      el(
        "p",
        "empty",
        `${rerank.scored}/${rerank.shortlist} candidates judged · ${pct(rerank.blend)} of the score kept from the deterministic hybrid`,
      ),
    );
    for (const hit of rerank.top || []) {
      const row = el("div", "prob");
      const left = el("div");
      left.append(el("div", "prob-label", hit.path));
      const track = el("div", "prob-track");
      const fill = el("div", "prob-fill");
      fill.style.width = `${Math.max(2, Math.min(100, (hit.p || 0) * 100))}%`;
      track.append(fill);
      left.append(track);
      row.append(left, el("div", "prob-value", `p ${(hit.p ?? 0).toFixed(2)} → ${(hit.score ?? 0).toFixed(2)}`));
      rerankGroup.append(row);
    }
    rerankGroup.append(
      el("p", "empty", "score = probability × recency decay × importance — policy stays in code, only relevance is judged"),
    );
  } else {
    rerankGroup.append(el("p", "empty", "No retrieval this turn, or the reranker was off — hybrid score used as-is."));
  }
  body.append(rerankGroup);

  // ── what was refused at the door ──
  const guards = of("guard");
  const guardGroup = el("div", "jgroup");
  guardGroup.append(el("p", "jgroup-title", "untrusted content"));
  if (guards.length) {
    for (const g of guards) {
      const row = el("div", "jrow");
      row.append(el("span", `chip chip-${g.screened ? (g.action === "pass" ? "pass" : g.action === "block" ? "block" : "review") : "off"}`, g.screened ? g.action : "unscreened"));
      row.append(el("span", "jrow-name", g.source || "external text"));
      row.append(
        el(
          "span",
          "jrow-val",
          g.screened ? `inject ${(g.injection ?? 0).toFixed(2)} · harm ${(g.severity ?? 0).toFixed(1)}` : g.reason || "",
        ),
      );
      guardGroup.append(row);
    }
  } else {
    guardGroup.append(el("p", "empty", "Nothing external was read this turn."));
  }
  body.append(guardGroup);

  // ── procedural memory: did a skill load, and why ──
  const skill = of("skill").pop();
  const skillGroup = el("div", "jgroup");
  skillGroup.append(el("p", "jgroup-title", "skill selection"));
  if (skill) {
    const row = el("div", "jrow");
    row.append(el("span", `chip ${skill.picked && skill.picked !== "none_of_these" ? "chip-on" : "chip-off"}`, skill.picked && skill.picked !== "none_of_these" ? skill.picked : "none"));
    row.append(el("span", "jrow-name", skill.screened ? `needs ${(skill.needs_skill ?? 0).toFixed(2)} · conf ${(skill.confidence ?? 0).toFixed(2)}` : skill.reason || ""));
    skillGroup.append(row);
  } else {
    skillGroup.append(el("p", "empty", "No skill judgement on this turn."));
  }
  body.append(skillGroup);

  // ── the write path ──
  const capture = of("capture").pop();
  const reflection = of("reflection").pop();
  const writeGroup = el("div", "jgroup");
  writeGroup.append(el("p", "jgroup-title", "write path"));
  if (capture) {
    const row = el("div", "jrow");
    row.append(el("span", `chip ${capture.captured ? "chip-on" : "chip-off"}`, capture.captured ? `captured [${Math.round(capture.importance ?? 0)}]` : "nothing kept"));
    row.append(el("span", "jrow-val", capture.reason || ""));
    writeGroup.append(row);
    if (capture.fact) writeGroup.append(el("p", "empty", capture.fact));
  } else {
    writeGroup.append(el("p", "empty", "Capture did not run on this turn."));
  }
  if (reflection) {
    const row = el("div", "jrow");
    const mode = reflection.mode || "";
    row.append(el("span", `chip ${mode === "background" ? "chip-on" : mode === "skipped" ? "chip-off" : "chip-review"}`, `reflection ${mode}`));
    row.append(
      el("span", "jrow-val", mode === "background" ? `${reflection.excerpts} excerpts · off the reply path` : reflection.reason || ""),
    );
    writeGroup.append(row);
  }
  body.append(writeGroup);

  // ── where the time went ──
  const entries = Object.entries(stages).filter(([, value]) => value > 0);
  const timeGroup = el("div", "jgroup");
  timeGroup.append(el("p", "jgroup-title", "where the time went"));
  if (entries.length) {
    const worst = Math.max(...entries.map(([, value]) => value));
    const waterfall = el("div", "waterfall");
    for (const [name, value] of entries.sort((a, b) => b[1] - a[1])) {
      const row = el("div", "wf-row");
      row.append(el("span", "wf-name", STAGE_LABELS[name] || name));
      const track = el("div", "wf-track");
      const fill = el("div", "wf-fill");
      fill.style.width = `${Math.max(3, (value / worst) * 100)}%`;
      track.append(fill);
      row.append(track, el("span", "wf-ms", ms(value)));
      waterfall.append(row);
    }
    timeGroup.append(waterfall);
  } else {
    timeGroup.append(el("p", "empty", "No timings recorded for this turn."));
  }
  body.append(timeGroup);
}

/* ── memory tiers ──────────────────────────────────────────────────────── */

document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((t) => {
      t.classList.remove("is-active");
      t.setAttribute("aria-selected", "false");
    });
    tab.classList.add("is-active");
    tab.setAttribute("aria-selected", "true");
    state.tier = tab.dataset.tier;
    renderTier();
  });
});

function tierText() {
  const m = state.mind || {};
  if (state.tier === "user") return m.user || "(no profile yet)";
  if (state.tier === "agents") return m.agents || "(no persona file)";
  if (state.tier === "dreams") return m.dreams_tail || "(no dreams yet)";
  return m.memory || "(empty — say hello and I will start writing)";
}

const TIER_LABEL = { memory: "MEMORY.md", user: "USER.md", agents: "AGENTS.md", dreams: "DREAMS.md" };

function renderTier() {
  $("tier-text").textContent = tierText();
  const m = state.mind;
  if (!m) return;
  // The note names the file actually on screen, so the panel never claims to
  // be showing one tier while displaying another.
  $("memory-note").textContent = `${TIER_LABEL[state.tier] || "MEMORY.md"} · ${m.today || ""}`;
  const prov = $("provenance");
  prov.replaceChildren();
  for (const [origin, count] of Object.entries((m.stats && m.stats.by_origin) || {})) {
    prov.append(el("span", `prov-chip prov-${origin}`, `${origin} ${count}`));
  }
  if (m.hallucination_flags) {
    prov.append(el("span", "prov-chip", `${m.hallucination_flags} unverified claims flagged`));
  }
}

/* ── decay curve ───────────────────────────────────────────────────────── */

const NS = "http://www.w3.org/2000/svg";

function drawCurve(curve, chunks) {
  const svg = $("curve");
  // Remove the previous drawing but keep <title>/<desc>: they are the SVG's
  // accessible name and description, and `aria-labelledby` points at them.
  // (Counting `childNodes` here was wrong — whitespace text nodes between the
  // tags counted too, so the first redraw deleted them and left the chart
  // pointing at ids that no longer existed.)
  for (const child of [...svg.children]) {
    if (child.tagName.toLowerCase() !== "title" && child.tagName.toLowerCase() !== "desc") {
      child.remove();
    }
  }
  const W = 640;
  const H = 220;
  const pad = { l: 34, r: 12, t: 14, b: 26 };

  const add = (tag, attrs) => {
    const node = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
    svg.append(node);
    return node;
  };

  for (let i = 0; i <= 4; i++) {
    const y = pad.t + (i / 4) * (H - pad.t - pad.b);
    add("line", { class: "curve-grid", x1: pad.l, y1: y, x2: W - pad.r, y2: y });
    const label = add("text", { class: "curve-axis", x: 4, y: y + 3 });
    label.textContent = `${100 - i * 25}%`;
  }

  const x = (age) => pad.l + (Math.min(age, 90) / 90) * (W - pad.l - pad.r);
  const y = (r) => H - pad.b - Math.max(0, Math.min(1, r)) * (H - pad.t - pad.b);

  if (curve && curve.length) {
    const points = curve
      .map((value, index) => `${x((index / Math.max(1, curve.length - 1)) * 90)} ${y(value)}`)
      .join(" ");
    add("polyline", { class: "curve-line", points });
  }

  for (const chunk of (chunks || []).slice(0, 120)) {
    const mote = add("circle", {
      class: "curve-mote",
      cx: x(chunk.age_days ?? 0),
      cy: y(chunk.retention ?? 1),
      r: 2.6,
    });
    mote.append(document.createElementNS(NS, "title"));
    mote.firstChild.textContent = `${chunk.path} · ${chunk.age_days ?? 0}d old · retention ${(chunk.retention ?? 1).toFixed(2)}`;
  }

  const ageAxis = add("text", { class: "curve-axis", x: W - pad.r - 68, y: H - 6 });
  ageAxis.textContent = "age (days) →";
}

/* ── lists ─────────────────────────────────────────────────────────────── */

function renderList(node, rows, emptyText, build) {
  node.replaceChildren();
  if (!rows || !rows.length) {
    node.append(el("p", "empty", emptyText));
    return;
  }
  for (const row of rows) node.append(build(row));
}

function refreshPanels() {
  return Promise.all([
    api("/api/mind"),
    api("/api/retention"),
    api("/api/rot"),
    api("/api/skills"),
    api("/api/tasks"),
    api("/api/costs"),
    api("/api/traces"),
    api("/api/jev"),
  ]).then(([mind, retention, rot, skills, tasks, costs, traces, jev]) => {
    if (!mind.error) {
      state.mind = mind;
      renderTier();
      $("dreams-text").textContent = mind.dreams_tail || "(no dreams yet)";
      $("dream-note").textContent = mind.hallucination_flags
        ? `${mind.hallucination_flags} unverified claims flagged`
        : "Light → REM → Deep";
      $("skills-note").textContent = `${(mind.skills || []).length} procedures written by Iris`;
    }

    if (retention.chunks) {
      const avg = retention.chunks.length
        ? retention.chunks.reduce((sum, c) => sum + (c.retention ?? 1), 0) / retention.chunks.length
        : 0;
      $("decay-note").textContent = `${retention.chunks.length} chunks · avg retention ${avg.toFixed(2)}`;
      drawCurve(retention.curve || [], retention.chunks);
    }

    if (rot.entries) {
      renderList($("rot-list"), rot.entries.slice(0, 5), "nothing has faded enough to forget — all fresh", (entry) => {
        const item = el("div", "item");
        const head = el("div", "item-head");
        head.append(el("span", null, `${entry.age_days}d`));
        head.append(el("span", "item-when", `retention ${(entry.retention ?? 0).toFixed(2)}`));
        item.append(head, el("div", "item-sub", entry.content));
        return item;
      });
    }

    if (skills.skills) {
      $("skills-note").textContent = `${skills.skills.length} procedures written by Iris`;
      renderList($("skill-list"), skills.skills, "no skills yet — teach her one in chat", (skill) => {
        const item = el("div", "item");
        const head = el("div", "item-head");
        head.append(el("span", null, skill.name));
        head.append(el("span", "item-when", `success ${(skill.success ?? 0).toFixed(1)}`));
        item.append(head, el("div", "item-sub", skill.description));
        return item;
      });
    }

    if (tasks.tasks) {
      $("tasks-note").textContent = tasks.tasks.length ? `${tasks.tasks.length} pending` : "nothing scheduled";
      renderList($("task-list"), tasks.tasks, "nothing scheduled", (task) => {
        const item = el("div", "item");
        const head = el("div", "item-head");
        head.append(el("span", null, task.run_at));
        item.append(head, el("div", "item-sub", task.instruction));
        return item;
      });
    }

    if (costs.totals) {
      const totals = costs.totals;
      $("vital-spend").textContent = usd(totals.cost);
      const box = $("spend-totals");
      box.replaceChildren();
      const stat = (label, value) => {
        const wrap = el("div", "spend-stat");
        wrap.append(el("dt", null, label), el("dd", null, value));
        box.append(wrap);
      };
      stat("total", usd(totals.cost));
      stat("requests", totals.requests ?? 0);
      stat("cache hit", pct(totals.cache_hit_rate || 0));
      stat("tokens", (totals.prompt_tokens ?? 0) + (totals.completion_tokens ?? 0));
      renderList(
        $("cost-list"),
        (costs.daily || []).slice(-7).reverse(),
        "no model calls recorded yet",
        (day) => {
          const item = el("div", "item");
          const head = el("div", "item-head");
          head.append(el("span", null, day.day));
          head.append(el("span", "item-when", usd(day.cost)));
          item.append(
            head,
            el(
              "div",
              "item-sub",
              `${day.requests} requests · ${day.prompt_tokens + day.completion_tokens} tokens · cache ${pct(day.cache_hit_rate || 0)}`,
            ),
          );
          return item;
        },
      );
    }

    if (traces.traces) {
      state.traces = traces.traces;
      $("traces-note").textContent = `${traces.traces.length} recent turns`;
      renderList($("trace-list"), traces.traces.slice(0, 12), "no turns yet — say something", (trace) => {
        const item = el("div", "item");
        const head = el("div", "item-head");
        head.append(el("span", null, ms(trace.latency_ms)));
        head.append(el("span", "item-when", trace.ts));
        item.append(head);
        const tools = (trace.tools || []).map((t) => t.name).join(", ");
        const counts = trace.counts
          ? Object.entries(trace.counts)
              .map(([k, v]) => `${k} ${v}`)
              .join(" · ")
          : "";
        item.append(el("div", "item-sub", trace.user || "—"));
        const meta = [tools ? `⚙ ${tools}` : "", trace.capture ? `💭 ${trace.capture}` : "", counts]
          .filter(Boolean)
          .join(" · ");
        if (meta) item.append(el("div", "item-sub", meta));
        if (trace.pending) item.append(el("div", "item-sub", "⏸ awaiting your approval"));
        return item;
      });
      renderJudgment(traces.traces[0]);
    }

    if (jev && !jev.error) state.jev = jev;
  });
}

/* ── dream cycle ───────────────────────────────────────────────────────── */

$("sleep-btn").addEventListener("click", async () => {
  const button = $("sleep-btn");
  button.disabled = true;
  button.textContent = "dreaming…";
  try {
    const res = await api("/api/sleep", "POST");
    addTurn(
      "iris",
      res.error
        ? `Dream cycle failed: ${res.error}`
        : `Dream cycle done — staged ${res.staged}, promoted ${res.promoted}, superseded ${res.superseded}.`,
    );
    await refreshPanels();
  } catch (err) {
    addTurn("iris", "Dream cycle failed: " + err.message);
  }
  button.disabled = false;
  button.textContent = "dream now";
});

/* ── boot ──────────────────────────────────────────────────────────────── */

refreshVitals();
refreshPanels();
setInterval(refreshVitals, 30000);
setInterval(refreshPanels, 60000);
