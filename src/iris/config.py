"""Central configuration for Iris.

Every tunable lives here — model tiers, memory budgets, recall constants.
These values are the *context engineering* contract: they decide how much
of the world fits into Iris's context window and how expensive each turn is.
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Model tiers ──────────────────────────────────────────────────────
    # Strong model: conversation, reasoning, skill writing.
    # Cheap model: extraction, consolidation, scoring (the write path).
    # Embedding model: index everything (OpenRouter and Groq have no
    # embeddings — Gemini does; fall back to Ollama nomic-embed-text if no
    # GEMINI_API_KEY is set).
    #
    # Provider pick: LLM_PROVIDER=auto|openrouter|groq|gemini|ollama.
    # auto = whichever key is present: gemini > groq > openrouter
    # (then ollama if nothing is configured).
    #
    # Keys you need, per provider:
    #   openrouter — OPENROUTER_API_KEY (openrouter.ai/keys; free models:
    #                openrouter_strong_model / _cheap_model below)
    #   groq       — GROQ_API_KEY (console.groq.com/keys)
    #   gemini     — GEMINI_API_KEY (aistudio.google.com/apikey)
    #   ollama     — no key; run `ollama serve` and `ollama pull <model>`
    #
    # Model ids are LiteLLM-style "provider/model" strings; the *:free suffix
    # selects OpenRouter free variants. Set *_MODEL env vars to override.
    llm_provider: str = "auto"

    # ── Secrets ──────────────────────────────────────────────────────────
    # All credential-bearing fields are `repr=False`: Settings is a plain
    # pydantic model, so any `log.info(settings)` / f-string / exception repr
    # would otherwise print every API key in cleartext. (Observed for real in
    # a pytest failure summary before this was added.)
    gemini_api_key: str = Field(default="", repr=False)
    strong_model: str = "gemini/gemini-3.5-flash"
    cheap_model: str = "gemini/gemini-3.1-flash-lite"
    embedding_model: str = "gemini/gemini-embedding-001"
    embedding_dim: int = 1536  # MRL: gemini-embedding-001 supports 128-3072

    # Groq ids were verified against console.groq.com/docs/models on 2026-09-21.
    # The previous defaults were both wrong at runtime: `qwen/qwen3.6-27b` no
    # longer exists (the live id is `qwen/qwen3.8-27b`) and
    # `groq/groq/compound-mini` had a doubled prefix *and* named a model
    # decommissioned that same day, so any Groq-keyed install failed over or
    # 404'd on every call. gpt-oss is on Groq's free tier (30 RPM / 1k RPD) and
    # is the current recommended general-purpose pair.
    groq_api_key: str = Field(default="", repr=False)
    groq_strong_model: str = "groq/openai/gpt-oss-120b"
    groq_cheap_model: str = "groq/openai/gpt-oss-20b"

    openrouter_api_key: str = Field(default="", repr=False)
    openrouter_strong_model: str = "openrouter/nvidia/nemotron-3-super-120b-a12b:free"
    openrouter_cheap_model: str = "openrouter/nvidia/nemotron-nano-9b-v2:free"

    ollama_base_url: str = "http://localhost:11434"
    ollama_strong_model: str = "ollama/qwen2.5-coder:3b"
    ollama_cheap_model: str = "ollama/qwen2.5-coder:3b"
    ollama_embedding_model: str = "ollama/nomic-embed-text"
    ollama_embedding_dim: int = 768

    # Web search (Tavily free tier). Empty = web_search tool says "not configured".
    tavily_api_key: str = Field(default="", repr=False)

    # Voice notes — Groq Whisper (needs groq_api_key).
    voice_model: str = "groq/whisper-large-v3-turbo"

    def model_post_init(self, __context) -> None:
        # Keep the per-provider model names before resolution overwrites
        # `strong_model`/`cheap_model` — the failover chain needs them.
        self._models_strong = {
            "gemini": self.strong_model,
            "groq": self.groq_strong_model,
            "openrouter": self.openrouter_strong_model,
            "ollama": self.ollama_strong_model,
        }
        self._models_cheap = {
            "gemini": self.cheap_model,
            "groq": self.groq_cheap_model,
            "openrouter": self.openrouter_cheap_model,
            "ollama": self.ollama_cheap_model,
        }
        provider = self.llm_provider.strip().lower()
        if provider == "auto":
            if self.gemini_api_key:
                provider = "gemini"
            elif self.groq_api_key:
                provider = "groq"
            elif self.openrouter_api_key:
                provider = "openrouter"
            else:
                provider = "ollama"
        self._resolved_provider = provider
        if provider == "openrouter":
            self.strong_model = self.openrouter_strong_model
            self.cheap_model = self.openrouter_cheap_model
        elif provider == "groq":
            self.strong_model = self.groq_strong_model
            self.cheap_model = self.groq_cheap_model
        elif provider == "ollama":
            self.strong_model = self.ollama_strong_model
            self.cheap_model = self.ollama_cheap_model
        # Embeddings stay Gemini when a key exists; otherwise fall back to
        # Ollama nomic-embed-text so OpenRouter/Groq-only setups still index.
        if not self.gemini_api_key:
            self.embedding_model = self.ollama_embedding_model
            self.embedding_dim = self.ollama_embedding_dim

    def llm_candidates(self, tier: str = "strong") -> list[tuple[str, str, dict]]:
        """Provider failover chain for a model tier.

        Returns (provider, model, auth) tuples ordered by priority: the
        resolved provider leads, the rest follow (only those with
        credentials), with local ollama as the always-available last resort.
        """
        models = self._models_strong if tier == "strong" else self._models_cheap
        order = ["gemini", "groq", "openrouter", "ollama"]
        lead = getattr(self, "_resolved_provider", None) or order[0]
        ordered = [lead, *[p for p in order if p != lead]]
        out: list[tuple[str, str, dict]] = []
        for provider in ordered:
            if provider == "ollama":
                auth = {"api_base": self.ollama_base_url}
            elif provider == "gemini":
                if not self.gemini_api_key:
                    continue
                auth = {"api_key": self.gemini_api_key}
            elif provider == "groq":
                if not self.groq_api_key:
                    continue
                auth = {"api_key": self.groq_api_key}
            elif provider == "openrouter":
                if not self.openrouter_api_key:
                    continue
                auth = {"api_key": self.openrouter_api_key}
            else:
                continue
            out.append((provider, models[provider], auth))
        return out

    # ── Infra ────────────────────────────────────────────────────────────
    postgres_dsn: str = Field(
        default="postgresql+psycopg://iris:iris_dev_password@localhost:5433/iris", repr=False
    )  # repr=False: the DSN embeds the database password
    telegram_bot_token: str = Field(default="", repr=False)
    telegram_mcp_url: str = "http://127.0.0.1:8100/mcp"
    owner_chat_id: int | None = None  # learned from the first /start if unknown
    workspace_dir: str = "./workspace"
    sandbox_dir: str = "./workspace/sandbox"  # the only file system Iris may touch
    iris_timezone: str = "UTC"

    # Shared-secret bearer auth for iris-core's HTTP API. Empty = auth is
    # disabled (dev default, logged at boot); set IRIS_API_TOKEN in .env to
    # require `Authorization: Bearer <token>` on every route except /health.
    iris_api_token: str = Field(default="", repr=False)

    # ── Context engineering (bootstrap budget) ───────────────────────────
    # How many tokens of curated memory may enter the prompt at session start.
    # Stable-prefix ordering makes this region cache-friendly with two-tier
    # model pricing (cache reads are ~10x cheaper than writes).
    bootstrap_budget_tokens: int = 4000
    user_profile_budget_tokens: int = 1500

    # ── Recall scoring ───────────────────────────────────────────────────
    # Exponential recency decay half-life for episodic (dated) memory.
    # Curated files (MEMORY.md, USER.md) are evergreen and never decay.
    recency_half_life_days: int = 30
    hybrid_top_k: int = 20
    mrr_top_k: int = 5  # after MMR diversity

    # ── Graph ────────────────────────────────────────────────────────────
    # Hard cap on LangGraph steps per turn (agent→tools cycles count 2 each).
    # Exceeding it is caught and turned into a graceful message, never a 500.
    graph_recursion_limit: int = 40

    # ── Compaction (context engineering) ─────────────────────────────────
    # When the serialized history exceeds the trigger, a compaction turn
    # flushes durable facts to the daily note, summarizes, and trims history
    # to the keep-budget (Pi/Claude Code pattern: bounded history forever).
    compaction_trigger_tokens: int = 12000
    compaction_keep_tokens: int = 2000
    compaction_summary_tokens: int = 400  # word cap on the injected summary

    # ── Prompt caching ───────────────────────────────────────────────────
    # LiteLLM caching=True keeps the stable prefix (system + memory context)
    # warm across calls, cutting repeated-prefix cost and latency. Cache-hit
    # tokens are recorded in the cost ledger and surfaced in /costs.
    llm_caching: bool = True

    # ── JEV — TypeSafe System One layer ──────────────────────────────────
    # Jev is not a chat model: it takes a *state* plus typed questions and
    # returns calibrated probabilities (Noul), distributions (Choice) and
    # graded positions (Score). Iris uses it where a judgment beats a
    # hand-tuned heuristic — recall reranking, skill selection, and screening
    # external text for instruction injection. Code keeps every threshold,
    # weight and action; Jev only supplies the judgment.
    #
    # Billed on input tokens only: $42/Btok ≈ $0.042/Mtok, output free
    # (https://docs.typesafe.ai/models). A full rerank is one request.
    #
    # Every integration is best-effort and degrades to the deterministic path
    # Iris already had, so the stack runs unchanged with TYPESAFE_API_KEY
    # unset (the default). Docs: https://docs.typesafe.ai
    typesafe_api_key: str = Field(default="", repr=False)  # env: TYPESAFE_API_KEY
    jev_enabled: bool = True
    jev_model: str = "jev-latest"  # alias → jev-1.13.0; pin the version to freeze behaviour
    jev_timeout_seconds: float = 12.0
    # Set by tooling (e.g. the eval lab) to force the deterministic path.
    jev_disabled_reason: str = ""

    # Rerank: Jev replaces the hybrid relevance term on the recall shortlist.
    # Decay/importance stay deterministic — they are product policy, not
    # relevance.
    jev_rerank_enabled: bool = True
    jev_rerank_candidates: int = 20
    jev_rerank_blend: float = 0.15  # weight kept for the deterministic hybrid score

    # Skill suggestion: one ranked judgment over the roster replaces
    # casefolded substring trigger matching.
    jev_skill_candidates: int = 40
    jev_skill_gate: float = 0.30
    jev_skill_min_confidence: float = 0.30

    # Guard: screen external text (ingested pages, search results) for
    # instruction-injection before Iris reads it. Fails open — untrusted
    # content stays untrusted either way.
    jev_guard_enabled: bool = True
    jev_guard_block_threshold: float = 0.70
    jev_guard_review_threshold: float = 0.35
    jev_guard_severity_block: float = 2.0
    jev_guard_scan_chars: int = 12_000

    # ── Capture (write-path safety net) ──────────────────────────────────
    # The v2 design deleted per-turn extraction on the assumption that the
    # agent would call `note` itself. Measured: 0 note calls in 36 traced
    # turns, so MEMORY.md only ever grew from compaction flush. Capture
    # restores the volume while keeping v2's actual win: a deterministic
    # prefilter decides whether to spend a judgment, so "ok"/"thanks" turns
    # still cost nothing. See iris/memory/capture.py.
    capture_enabled: bool = True
    capture_min_chars: int = 25  # below this a turn cannot carry a durable fact
    capture_use_llm: bool = True  # cheap-tier fallback when JEV is unavailable
    capture_min_importance: float = 4.0  # 0-10 floor; also gates the Light phase
    capture_gate: float = 0.60  # JEV P(durable) needed to capture
    capture_already_known_gate: float = 0.50  # JEV P(already in context) to skip
    capture_context_chars: int = 4000  # assembled prefix shown to the judgment
    capture_max_per_day: int = 50  # runaway guard on the daily note

    # ── Post-reply passes ────────────────────────────────────────────────
    # The reflection pass (hallucination triage) only appends to a telemetry
    # file: it cannot change the reply, the memory or the trace, so it has no
    # business blocking the turn. Backgrounding it removes one cheap-tier
    # completion (~2-6 s) from the tail of every retrieval-backed turn. Set
    # false to run it inline (deterministic tests, or when a run must be
    # self-contained by the time it returns).
    reflection_background: bool = True
    # How long `background.drain()` waits on shutdown before letting go.
    background_drain_timeout: float = 10.0

    # Per-turn observation buffer: JEV judgments + stage timings, written to
    # the trace so the judgment layer is inspectable. See iris/turnlog.py.
    turnlog_enabled: bool = True

    # ── Recall cache (semantic memory cache) ─────────────────────────────
    # In-memory query→hits cache in front of search/escalate. Disable for
    # deterministic eval-lab runs (IRIS_RECALL_CACHE=0).
    recall_cache_enabled: bool = True
    recall_cache_ttl_seconds: float = 60.0

    # ── Indexing ─────────────────────────────────────────────────────────
    chunk_tokens: int = 400
    chunk_overlap_tokens: int = 80

    # ── Contextual chunking ───────────────────────────────────────────────
    # Cheap-model context headers (up to context_header_tokens) prepended to
    # each chunk before embedding, so vectors carry document-level context
    # (Anthropic-style contextual retrieval). Cached per file by content
    # hash in .dreams/contexts/; plain chunks are the fallback.
    contextual_chunking_enabled: bool = True
    context_header_tokens: int = 60

    # ── Sleep / dreaming ─────────────────────────────────────────────────
    nightly_sleep_hour: int = 4  # best-effort nightly sweep (24h clock, local tz)
    morning_brief_hour: int = 8  # Telegram digest to owner_chat_id (needs it set)
    # Deterministic promotion gate (Light phase, no model calls).
    # Score = w·[occurrence, importance, richness, trigger-diversity, recall].
    dream_light_weights: tuple[float, float, float, float, float] = (0.25, 0.30, 0.10, 0.10, 0.25)
    dream_gate_score: float = 0.5
    dream_gate_importance: float = 6.0
    # Light phase also scans the last N daily notes for agent-written
    # "(note)" lines — the episodic write surface since the write-path
    # extraction pipeline was removed.
    dream_note_scan_days: int = 7

    # ── Recall feedback ───────────────────────────────────────────────────
    # Every memory_search hit is logged to .dreams/recall_feedback.jsonl
    # (rotated at max bytes). The Light phase uses recall counts as a 5th
    # signal, so memories the agent actually goes back to are promoted faster.
    recall_feedback_enabled: bool = True
    recall_feedback_max_bytes: int = 200_000

    # ── Turn traces ───────────────────────────────────────────────────────
    # One JSON line per chat turn in config/traces.jsonl (rotated at max
    # bytes), surfaced on the dashboard's /traces panel.
    trace_max_bytes: int = 1_000_000

    # ── Forgetting ───────────────────────────────────────────────────────
    # Retention below this fraction marks a memory as rot (flagged in dreams,
    # owner decides; nothing is hard-deleted silently).
    rot_threshold: float = 0.2


settings = Settings()
