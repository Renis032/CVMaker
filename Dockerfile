# CVMaker server image (CVMAKER_MODE=server). The built editor (cvmaker/static/editor) is committed, so no Node step.
#   docker compose up        - see docker-compose.yml
FROM python:3.12-slim-bookworm

# Chromium renders PDFs and JavaScript-built career pages; fonts for CVs.
RUN apt-get update && apt-get install -y --no-install-recommends \
        chromium fonts-dejavu fonts-liberation fonts-noto-core poppler-utils ca-certificates tini \
    && rm -rf /var/lib/apt/lists/*

# Chrome's own sandbox needs user namespaces, which Docker's default seccomp profile (and AppArmor on Ubuntu hosts)
# block. Inside this image the container is the sandbox instead: a non-root user, and docker-compose.yml drops all
# capabilities (and can set no-new-privileges). Outside a container, never set CVMAKER_CHROME_NO_SANDBOX.
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    CVMAKER_MODE=server CVMAKER_BACKEND=api CVMAKER_DATA=/data CHROME_BIN=/usr/bin/chromium \
    CVMAKER_TASK_WORKERS=0 CVMAKER_CHROME_NO_SANDBOX=1

WORKDIR /app
# Install exactly what uv.lock pins (not whatever versions pip resolves on build day).
COPY --from=ghcr.io/astral-sh/uv:0.13 /uv /usr/local/bin/uv
ENV UV_PROJECT_ENVIRONMENT=/opt/venv UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PATH="/opt/venv/bin:$PATH"
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --extra server --no-install-project
COPY cvmaker ./cvmaker
COPY companies.yaml ./
RUN uv sync --frozen --no-dev --extra server --no-editable

# Run as an unprivileged user.
RUN useradd --create-home --uid 10001 cvmaker && mkdir -p /data && chown cvmaker /data
USER cvmaker
VOLUME /data
EXPOSE 8000
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["uvicorn", "cvmaker.app:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*", "--workers", "2"]
