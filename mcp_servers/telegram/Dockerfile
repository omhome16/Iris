# The bridge is a *client of the library*, not a second brain: it imports
# `iris_ai.channels.brain` and `iris_ai.channels.updates`, so `iris_ai` has to be
# importable inside this image.
#
# Built from the repository root (see docker-compose.yml for the context), then
# installed with --no-deps on purpose: the bridge only speaks HTTP to iris-core,
# so langgraph / asyncpg / litellm / pgvector have no business in this image.
# `requirements.txt` covers what the library modules it imports actually need.
FROM python:3.12-slim

WORKDIR /app

COPY mcp_servers/telegram/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# README.md is here because pyproject declares it as the project readme — the
# hatchling build fails without it.
COPY pyproject.toml README.md ./
COPY src ./src
COPY mcp_servers/telegram/server.py ./
RUN pip install --no-cache-dir --no-deps .

CMD ["python", "server.py"]
