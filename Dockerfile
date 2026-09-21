# Iris production image.
#
# Two stages: a builder with compilers, and a slim runtime that carries only
# the installed package. Non-root, no build toolchain, no .env — configuration
# arrives through the environment (see .env.example). The workspace is a
# mounted volume, never baked into the image.

FROM python:3.13-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build

# Build deps only exist in this stage. asyncpg/psycopg wheels usually resolve,
# but gcc keeps source builds working on architectures without wheels.
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc g++ libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy metadata first so the dependency layer caches independently of source.
COPY pyproject.toml README.md ./
COPY src ./src

RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install .


FROM python:3.13-slim AS runtime

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    WORKSPACE_DIR=/data/workspace \
    SANDBOX_DIR=/data/workspace/sandbox

# curl is for the HEALTHCHECK; libpq keeps psycopg's binary build happy.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl libpq5 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 iris

COPY --from=builder /opt/venv /opt/venv

# The workspace is the product's data directory: it must be a volume, never
# baked into the image. Created up front and owned by the runtime user so a
# fresh *named* volume is writable. A bind-mounted host directory keeps the
# host's ownership instead — on Linux, chown it to uid 10001 (see
# docs/deployment.md) or Iris cannot write memory.
RUN mkdir -p /data/workspace /app && chown -R iris:iris /data /app

USER iris
WORKDIR /app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/health || exit 1

CMD ["uvicorn", "iris.api:app", "--host", "0.0.0.0", "--port", "8000"]
