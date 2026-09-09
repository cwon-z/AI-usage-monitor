FROM node:26-bookworm-slim AS cli

ARG CLAUDE_CODE_VERSION=2.1.266
ARG CODEX_VERSION=0.150.1
RUN npm install --global --omit=dev \
    "@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}" \
    "@openai/codex@${CODEX_VERSION}"

FROM python:3.12-slim-bookworm

ARG APP_UID=10001
ARG APP_GID=10001

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/usr/local/bin:${PATH}"

RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates libstdc++6 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=cli /usr/local/bin/node /usr/local/bin/node
COPY --from=cli /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s /usr/local/lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe /usr/local/bin/claude \
    && ln -s /usr/local/lib/node_modules/@openai/codex/bin/codex.js /usr/local/bin/codex \
    && test "$APP_UID" -gt 0 && test "$APP_GID" -gt 0 \
    && (getent group "$APP_GID" >/dev/null || groupadd --gid "$APP_GID" app) \
    && useradd --uid "$APP_UID" --gid "$APP_GID" --create-home app \
    && mkdir -p /app /data \
    && chown -R "$APP_UID:$APP_GID" /app /data

WORKDIR /app
COPY pyproject.toml requirements.lock README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir --constraint requirements.lock . && pip check

USER app
RUN claude --version && codex --version
EXPOSE 8000
VOLUME ["/data"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=3)" || exit 1

CMD ["uvicorn", "ai_usage_monitor.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
