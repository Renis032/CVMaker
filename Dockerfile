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
COPY pyproject.toml uv.lock README.md ./
COPY cvmaker ./cvmaker
COPY companies.yaml ./
RUN pip install --no-cache-dir ".[server]"

# Run as an unprivileged user.
RUN useradd --create-home --uid 10001 cvmaker && mkdir -p /data && chown cvmaker /data
USER cvmaker
VOLUME /data
EXPOSE 8000
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["uvicorn", "cvmaker.app:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*", "--workers", "2"]
