# The whole platform in one image: the FastAPI backend, plus the Mini App
# (React, frontend/) built and served by it at /app/.
#
#   docker build -t cloth-store .
#   docker run --env-file backend/.env -p 8000:8000 cloth-store
#
# Build it from the merged code (main), which has both backend/ and frontend/.
# On a branch without frontend/ (backend-scaffold) it still builds: /app/
# then shows the small placeholder page.
#
# Run ONE container only: customer locks and spam limits live in memory
# (BUILD_PLAN.md, Phase 7 and 10).

# --- 1. The Mini App --------------------------------------------------------------
FROM node:22-alpine AS frontend
# The npm that made frontend/package-lock.json: npm 10 (Node 22's own) reads
# its optional test dependencies differently and `npm ci` refuses the lock file.
RUN npm install -g npm@11.11.0 --no-audit --no-fund
WORKDIR /src
COPY . .
RUN if [ -f frontend/package.json ]; then \
      cd frontend && npm ci --no-audit --no-fund && npm run build; \
    else \
      echo "No frontend/ here: the backend will serve its placeholder page." && mkdir -p frontend/dist; \
    fi

# --- 2. The backend ---------------------------------------------------------------
FROM python:3.12-slim AS app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=8000

WORKDIR /srv

# Dependencies first, so code changes don't reinstall them.
COPY backend/requirements.txt backend/requirements.txt
RUN pip install -r backend/requirements.txt

COPY backend/app backend/app
COPY backend/scripts backend/scripts
COPY --from=frontend /src/frontend/dist frontend/dist

# Not root.
RUN useradd --create-home --uid 10001 app && chown -R app /srv
USER app
WORKDIR /srv/backend

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\", \"8000\")}/api/v1/health', timeout=4)"

# One worker (see above). --proxy-headers: the host's HTTPS proxy sits in front.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --workers 1 --proxy-headers --forwarded-allow-ips '*'"]
