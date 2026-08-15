"""Central configuration for Iris.

Every tunable lives here — model tiers, memory budgets, recall constants.
These values are the *context engineering* contract: they decide how much
of the world fits into Iris's context window and how expensive each turn is.
"""

from __future__ import annotations

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
    # Embedding model: index everything (Groq has no embeddings — stays Gemini).
    #
    # Defaults: Gemini free tier (one key from aistudio.google.com/apikey
    # covers all three). If GROQ_API_KEY is set, the strong/cheap tiers swap
    # to Groq's generous free tier automatically; override with
    # GROQ_STRONG_MODEL / GROQ_CHEAP_MODEL.
    strong_model: str = "gemini/gemini-3.5-flash"
    cheap_model: str = "gemini/gemini-3.1-flash-lite"
    embedding_model: str = "gemini/gemini-embedding-001"
    embedding_dim: int = 1536  # MRL: gemini-embedding-001 supports 128-3072

    groq_api_key: str = ""
    groq_strong_model: str = "groq/llama-3.3-70b-versatile"
    groq_cheap_model: str = "groq/llama-3.1-8b-instant"

    # Web search (Tavily free tier). Empty = web_search tool says "not configured".
    tavily_api_key: str = ""

    # Voice notes — Groq Whisper (needs groq_api_key).
    voice_model: str = "groq/whisper-large-v3-turbo"

    def model_post_init(self, __context) -> None:
        if self.groq_api_key and self.strong_model.startswith("gemini/"):
            self.strong_model = self.groq_strong_model
            self.cheap_model = self.groq_cheap_model

    # ── Infra ────────────────────────────────────────────────────────────
    postgres_dsn: str = "postgresql+psycopg://iris:iris_dev_password@localhost:5433/iris"
    telegram_bot_token: str = ""
    telegram_mcp_url: str = "http://127.0.0.1:8100/mcp"
    owner_chat_id: int | None = None  # learned from the first /start if unknown
    workspace_dir: str = "./workspace"
    sandbox_dir: str = "./workspace/sandbox"  # the only file system Iris may touch
    iris_timezone: str = "UTC"

    # ── Context engineering (bootstrap budget) ───────────────────────────
    # How many tokens of curated memory may enter the prompt at session start.
    # Stable-prefix ordering makes this region cache-friendly with two-tier
    # model pricing (cache reads are ~10x cheaper than writes).
    bootstrap_budget_tokens: int = 4000
    user_profile_budget_tokens: int = 1500

    # ── Trigger injection ────────────────────────────────────────────────
    # Fast prefilter: entries with trigger phrases matching an inbound message
    # at or above this score may be injected as a compact context block.
    trigger_threshold: float = 0.72
    trigger_inject_max: int = 3

    # ── Recall scoring ───────────────────────────────────────────────────
    # Exponential recency decay half-life for episodic (dated) memory.
    # Curated files (MEMORY.md, USER.md) are evergreen and never decay.
    recency_half_life_days: int = 30
    hybrid_top_k: int = 20
    mrr_top_k: int = 5  # after MMR diversity

    # ── Indexing ─────────────────────────────────────────────────────────
    chunk_tokens: int = 400
    chunk_overlap_tokens: int = 80

    # ── Sleep / dreaming ─────────────────────────────────────────────────
    dream_staging_dir: str = "memory/.dreams"
    dream_candidate_max: int = 200
    nightly_sleep_hour: int = 4  # best-effort nightly sweep (24h clock, local tz)
    # Deterministic promotion gate (Light phase, no model calls).
    # Score = w·[occurrence, importance, richness, trigger-diversity].
    dream_light_weights: tuple[float, float, float, float] = (0.35, 0.35, 0.15, 0.15)
    dream_gate_score: float = 0.5
    dream_gate_importance: float = 6.0

    # ── Forgetting ───────────────────────────────────────────────────────
    # Retention below this fraction marks a memory as rot (flagged in dreams,
    # owner decides; nothing is hard-deleted silently).
    rot_threshold: float = 0.2


settings = Settings()