# Images de production (étape 19). AUCUN secret : .env.production est chiffré (dotenvx) et la
# clé DOTENV_PRIVATE_KEY_PRODUCTION n'est injectée qu'à l'exécution, jamais au build.
#
#   docker build --target api         -t eduvia-visio-api .
#   docker build --target transcriber -t eduvia-visio-transcriber .                 (CPU)
#   docker build --target transcriber --build-arg WITH_CUDA=1 -t ...-transcriber .   (GPU)

# ---- interface (Vite) --------------------------------------------------------------------
FROM node:22-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

# ---- base Python commune : dépendances verrouillées par uv.lock -------------------------------
FROM python:3.12-slim AS base
ARG DOTENVX_VERSION=2.38.2
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 UV_COMPILE_BYTECODE=0 UV_LINK_MODE=copy
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
    && curl -sfS "https://dotenvx.sh?version=${DOTENVX_VERSION}" | sh \
    && apt-get purge -y curl && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /usr/local/bin/uv
WORKDIR /app
RUN useradd --system --uid 10001 --home /app visio && mkdir -p /app/data && chown -R visio /app
COPY pyproject.toml uv.lock README.md alembic.ini ./
COPY src/ src/
COPY .env.production ./.env.production
ENV PATH="/app/.venv/bin:$PATH" APP_ENV=production

# ---- API + planificateur + interface ------------------------------------------------------
FROM base AS api
# ffmpeg : fusion des segments d'enregistrement (RECORDING_SEGMENT_SECONDS > 0)
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
RUN uv sync --frozen --no-dev
COPY --from=web /web/dist /app/web/dist
COPY THIRD-PARTY-LICENSES.txt /app/THIRD-PARTY-LICENSES.txt
COPY deploy/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh && chown -R visio /app
USER visio
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=5 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=4).status==200 else 1)"
ENTRYPOINT ["entrypoint.sh"]
CMD ["api"]

# ---- worker de transcription (faster-whisper, local) ---------------------------------------
FROM base AS transcriber
ARG WITH_CUDA=0
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
RUN uv sync --frozen --no-dev --extra transcription \
    && if [ "$WITH_CUDA" = "1" ]; then uv pip install --python /app/.venv/bin/python "nvidia-cublas-cu12" "nvidia-cudnn-cu12==9.*"; fi
COPY deploy/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh && mkdir -p /app/data/models/whisper && chown -R visio /app
USER visio
# Chemin des bibliothèques CUDA installées par pip (sans effet en CPU).
ENV LD_LIBRARY_PATH=/app/.venv/lib/python3.12/site-packages/nvidia/cublas/lib:/app/.venv/lib/python3.12/site-packages/nvidia/cudnn/lib
ENTRYPOINT ["entrypoint.sh"]
CMD ["transcriber"]
