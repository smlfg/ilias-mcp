# Sandbox image for Glama's schema inspection (STANDARD G6). stdio only, no ports.
# Without a stored ilias-cli session every network tool answers not_logged_in.
FROM python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1

COPY --from=ghcr.io/astral-sh/uv:0.13.1 /uv /usr/local/bin/uv
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 mcp

WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src ./src
COPY schemas ./schemas
RUN uv sync --locked --no-dev && chown -R mcp:mcp /app

USER mcp
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHON_KEYRING_BACKEND=keyring.backends.null.Keyring
ENTRYPOINT ["ilias-mcp"]
