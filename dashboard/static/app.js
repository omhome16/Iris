/* Iris console client — chat + collapsible memory sidebar. Vanilla JS. */

const $ = (id) => document.getElementById(id);

const state = {
  session: "web-" + Math.random().toString(36).slice(2, 8),
  busy: false,
  memoryTab: "memory",
  memoryCache: null,
};

async function api(path, method = "GET", body = null) {
  const opts = { method, headers: {} };
  if (body) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const r = await fetch(path, opts);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) data.error = data.error || `HTTP ${r.status}`;
  return data;
}

/* ── Theme ─────────────────────────────────────────────────────── */

const themePref = localStorage.getItem("iris-theme");
if (themePref === "dark" || themePref === "light") {
  document.documentElement.dataset.theme = themePref;
} else if (window.matchMedia("(prefers-color-scheme: dark)").matches) {
  document.documentElement.dataset.theme = "dark";
}

$("theme-toggle").addEventListener("click", () => {
  const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  localStorage.setItem("iris-theme", next);
});

/* ── Sidebar ───────────────────────────────────────────────────── */

const SIDEBAR_KEY = "iris-sidebar-collapsed";

function setSidebar(collapsed) {
  $("sidebar").classList.toggle("collapsed", collapsed);
  localStorage.setItem(SIDEBAR_KEY, collapsed ? "1" : "0");
}

function isNarrow() {
  return window.matchMedia("(max-width: 860px)").matches;
}

function initSidebar() {
  const saved = localStorage.getItem(SIDEBAR_KEY);
  setSidebar(saved === "1" ? true : saved === "0" ? false : isNarrow());
}

$("side-toggle").addEventListener("click", () => {
  setSidebar(!$("sidebar").classList.contains("collapsed"));
});

document.querySelectorAll(".side-head").forEach((head) => {
  head.addEventListener("click", () => {
    const section = head.closest(".side-section");
    if ($("sidebar").classList.contains("collapsed")) {
      setSidebar(false);
      section.classList.add("open");
      head.setAttribute("aria-expanded", "true");
      return;
    }
    const willOpen = !section.classList.contains("open");
    section.classList.toggle("open", willOpen);
    head.setAttribute("aria-expanded", String(willOpen));
  });
});

/* ── Status ────────────────────────────────────────────────────── */

async function refreshStatus() {
  try {
    const h = await api("/api/health");
    const s = h.memory || {};
    const el = $("status");
    el.classList.add("online");
    $("status-text").textContent = "online";
    const by = Object.entries(s.by_origin || {})
      .map(([k, v]) => `${k} ${v}`)
      .join(" · ");
    $("index-stats").textContent = `index: ${s.total_chunks ?? "—"} chunks${by ? " · " + by : ""}`;
  } catch {
    $("status-text").textContent = "core unreachable";
  }
}

/* ── Chat ──────────────────────────────────────────────────────── */

function addMsg(role, text) {
  const log = $("chat-log");
  const div = document.createElement("div");
  div.className = `msg msg-${role}`;
  const r = document.createElement("div");
  r.className = "msg-role";
  r.textContent = role === "user" ? "you" : "iris";
  const b = document.createElement("div");
  b.className = "msg-body";
  b.textContent = text;
  div.append(r, b);
  log.appendChild(div);
  log.scrollTop = log.scrollHeight;
}

$("composer").addEventListener("submit", async (e) => {
  e.preventDefault();
  const input = $("input");
  const text = input.value.trim();
  if (!text || state.busy) return;
  input.value = "";
  addMsg("user", text);
  state.busy = true;
  $("send-btn").disabled = true;
  $("hint").textContent = "iris is thinking…";
  const body = $("activity-body");
  body.innerHTML = "";
  let reply = "";
  try {
    const res = await fetch("/api/chat/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text, session_id: state.session }),
    });
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let idx;
      while ((idx = buf.indexOf("\n")) >= 0) {
        const line = buf.slice(0, idx).trim();
        buf = buf.slice(idx + 1);
        if (!line.startsWith("data: ")) continue;
        let ev;
        try { ev = JSON.parse(line.slice(6)); } catch { continue; }
        if (ev.kind === "text") {
          reply += ev.delta || "";
          $("hint").textContent = "iris is typing…";
        } else if (ev.kind === "reply") {
          reply = ev.text || reply;
        } else if (ev.kind === "thinking" && ev.delta) {
          const row = document.createElement("div");
          row.className = "act-thinking";
          row.textContent = ev.delta;
          body.appendChild(row);
          body.scrollTop = body.scrollHeight;
        } else if (ev.kind === "tool_call" && ev.call) {
          const row = document.createElement("div");
          row.className = "act-tool";
          row.textContent = "🔧 " + (ev.call.name || "tool") + " " + JSON.stringify(ev.call.args || {});
          body.appendChild(row);
          body.scrollTop = body.scrollHeight;
        }
      }
    }
    addMsg("iris", reply || "(no reply)");
    $("hint").textContent = "";
    refreshPanels();
  } catch (err) {
    addMsg("iris", "Could not reach iris-core: " + err.message);
    $("hint").textContent = "";
  }
  state.busy = false;
  $("send-btn").disabled = false;
  input.focus();
});

/* ── Sidebar panels ────────────────────────────────────────────── */

async function refreshPanels() {
  const [mind, ret, rot, skills, tasks, costs, traces] = await Promise.all([
    api("/api/mind"),
    api("/api/retention"),
    api("/api/rot"),
    api("/api/skills"),
    api("/api/tasks"),
    api("/api/costs"),
    api("/api/traces"),
  ]);

  if (mind.memory !== undefined) {
    state.memoryCache = mind;
    renderMemoryTab();
    $("dreams-body").textContent = mind.dreams_tail || "(no dreams yet)";
    const flags = mind.hallucination_flags ?? 0;
    $("flags-num").textContent = flags;
    $("count-flags").textContent = flags > 0 ? flags : "";
  }

  if (ret.chunks) {
    $("count-chunks").textContent = ret.chunks.length;
    const avg = ret.chunks.length
      ? (ret.chunks.reduce((a, c) => a + (c.retention ?? 1), 0) / ret.chunks.length).toFixed(2)
      : "—";
    $("memory-meta").textContent =
      "avg retention " + avg + " · " +
      (mind.stats && mind.stats.by_origin
        ? Object.entries(mind.stats.by_origin).map(([k, v]) => `${k} ${v}`).join(" · ")
        : "by origin");
    drawCurve(ret.curve || [], ret.chunks);
  }

  if (rot.entries) {
    $("count-rot").textContent = rot.count ?? rot.entries.length;
    const list = $("rot-list");
    list.innerHTML = "";
    if (!rot.entries.length) {
      list.innerHTML = '<div class="empty">no decayed memories — all fresh</div>';
    } else {
      for (const e of rot.entries.slice(0, 4)) {
        const item = document.createElement("div");
        item.className = "rot-item";
        const c = document.createElement("span");
        c.textContent = e.content.slice(0, 52);
        const a = document.createElement("span");
        a.className = "rot-age";
        a.textContent = `${e.age_days}d · ${e.retention.toFixed(2)}`;
        item.append(c, a);
        list.appendChild(item);
      }
    }
  }

  if (skills.skills) {
    $("count-skills").textContent = skills.skills.length;
    const list = $("skill-list");
    if (!skills.skills.length) {
      list.innerHTML = '<div class="empty">no skills yet — teach me one in chat</div>';
    } else {
      list.innerHTML = "";
      for (const s of skills.skills) {
        const item = document.createElement("div");
        item.className = "skill-item";
        const n = document.createElement("div");
        n.className = "skill-name";
        n.textContent = s.name;
        const d = document.createElement("div");
        d.className = "skill-desc";
        d.textContent = s.description;
        item.append(n, d);
        list.appendChild(item);
      }
    }
  }

  if (tasks.tasks) {
    $("count-tasks").textContent = tasks.tasks.length;
    const list = $("task-list");
    if (!tasks.tasks.length) {
      list.innerHTML = '<div class="empty">no scheduled tasks</div>';
    } else {
      list.innerHTML = "";
      for (const t of tasks.tasks) {
        const item = document.createElement("div");
        item.className = "skill-item";
        const n = document.createElement("div");
        n.className = "skill-name";
        n.textContent = t.run_at;
        const d = document.createElement("div");
        d.className = "skill-desc";
        d.textContent = t.instruction;
        item.append(n, d);
        list.appendChild(item);
      }
    }
  }

  if (costs.totals !== undefined) {
    $("count-cost").textContent = "$" + costs.totals.cost.toFixed(2);
    const hit = (costs.totals.cache_hit_rate || 0) * 100;
    $("cost-cache").textContent = "cache hit: " + hit.toFixed(1) + "%";
    const list = $("cost-list");
    list.innerHTML = "";
    for (const d of costs.daily.slice(0, 7)) {
      const item = document.createElement("div");
      item.className = "skill-item";
      const n = document.createElement("div");
      n.className = "skill-name";
      n.textContent = d.day;
      const c = document.createElement("div");
      c.className = "skill-desc";
      c.textContent = `$${d.cost.toFixed(3)} · ${d.requests} req · ${d.prompt_tokens + d.completion_tokens} tok` +
        (d.cache_hit_rate ? ` · cache ${(d.cache_hit_rate * 100).toFixed(1)}%` : "");
      item.append(n, c);
      list.appendChild(item);
    }
  }

  if (traces.traces) {
    const list = $("trace-list");
    list.innerHTML = "";
    if (!traces.traces.length) {
      list.innerHTML = '<div class="empty">no turns yet — say something in chat</div>';
    } else {
      for (const t of traces.traces.slice(0, 10)) {
        const item = document.createElement("div");
        item.className = "skill-item";
        const n = document.createElement("div");
        n.className = "skill-name";
        n.textContent = `${t.ts} · ${t.latency_ms}ms` + (t.pending ? " · ⏸ approval" : "");
        const d = document.createElement("div");
        d.className = "skill-desc";
        const tools = (t.tools || []).map((x) => x.name).join(", ");
        d.textContent = `you: ${t.user || "—"}${tools ? ` · 🔧 ${tools}` : ""}`;
        item.append(n, d);
        list.appendChild(item);
      }
    }
  }
}

function renderMemoryTab() {
  const m = state.memoryCache;
  if (!m) return;
  const body = $("file-body");
  if (state.memoryTab === "memory") {
    body.textContent = m.memory || "(empty — say hello to begin)";
  } else {
    body.textContent = m.user || "(no profile yet)";
  }
}

document.querySelectorAll(".file-tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".file-tab").forEach((t) => t.classList.remove("active"));
    tab.classList.add("active");
    state.memoryTab = tab.dataset.file;
    renderMemoryTab();
  });
});

/* ── Decay curve ──────────────────────────────────────────────── */

function drawCurve(curve, chunks) {
  const svg = $("curve");
  const W = 600, H = 180, pad = 14;
  let out = "";
  for (let i = 0; i <= 4; i++) {
    const y = pad + (i / 4) * (H - pad * 2);
    out += `<line class="curve-grid" x1="0" y1="${y}" x2="${W}" y2="${y}"/>`;
  }
  const x = (age) => pad + (Math.min(age, 90) / 90) * (W - pad * 2);
  const y = (r) => H - pad - Math.max(0, Math.min(1, r)) * (H - pad * 2);
  if (curve && curve.length) {
    const pts = curve.map((p, i) => `${x(i)} ${y(p)}`).join(" ");
    out += `<polyline class="curve-line" points="${pts}"/>`;
  }
  for (const c of (chunks || []).slice(0, 80)) {
    out += `<circle class="curve-chunk" cx="${x(c.age_days ?? 0)}" cy="${y(c.retention ?? 1)}" r="2.5"/>`;
  }
  svg.innerHTML = out;
}

/* ── Dream cycle ──────────────────────────────────────────────── */

$("sleep-btn").addEventListener("click", async () => {
  const btn = $("sleep-btn");
  btn.disabled = true;
  btn.textContent = "dreaming…";
  try {
    const res = await api("/api/sleep", "POST");
    addMsg("iris", `Dream cycle complete — staged ${res.staged}, promoted ${res.promoted}, added ${res.added}, superseded ${res.superseded}.`);
    refreshPanels();
  } catch (err) {
    addMsg("iris", "Dream cycle failed: " + err.message);
  }
  btn.disabled = false;
  btn.textContent = "run dream cycle";
});

/* ── Boot ─────────────────────────────────────────────────────── */

initSidebar();
refreshStatus();
refreshPanels();
setInterval(refreshStatus, 30000);
setInterval(refreshPanels, 60000);