# syntax=docker/dockerfile:1.7
# AAP application image: api, worker, migrate, ebs-cm and model-fetch all use it.

ARG PYTHON_IMAGE=python:3.12-slim-bookworm

# --------------------------------------------------------------------- base
FROM ${PYTHON_IMAGE} AS base
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
# WeasyPrint (invoice generator) needs Pango/HarfBuzz + fonts; OpenCV needs glib.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b libharfbuzz-subset0 \
      libfontconfig1 fonts-dejavu-core fonts-liberation fonts-noto-core \
      libglib2.0-0 curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# -------------------------------------------------------------------- build
FROM base AS build
COPY --from=ghcr.io/astral-sh/uv:0.8 /uv /uvx /bin/
ARG INSTALL_DEV=1
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /app
COPY pyproject.toml uv.lock* ./
# Dependencies first (cached layer). --frozen when a lock file is committed.
RUN --mount=type=cache,target=/root/.cache/uv \
    FROZEN=$([ -f uv.lock ] && echo --frozen); \
    DEV=$([ "$INSTALL_DEV" = "1" ] || echo --no-dev); \
    uv sync --no-install-project $FROZEN $DEV
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    FROZEN=$([ -f uv.lock ] && echo --frozen); \
    DEV=$([ "$INSTALL_DEV" = "1" ] || echo --no-dev); \
    uv sync $FROZEN $DEV

# ------------------------------------------------------------------ runtime
FROM base AS runtime
COPY --from=build /opt/venv /opt/venv
WORKDIR /app
COPY pyproject.toml alembic.ini ./
COPY src ./src
COPY tools ./tools
COPY tests ./tests
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONPATH=/app:/app/src \
    FILESTORE_DIR=/data/filestore
# Directories that named volumes mount over must exist here, owned by aap, so the
# volumes inherit that ownership on first mount.
RUN useradd --create-home --uid 1000 aap \
 && mkdir -p /data/filestore /models /home/aap/.cache/huggingface \
 && chown -R aap:aap /data /models /app /home/aap/.cache
USER aap
EXPOSE 8080
CMD ["uvicorn", "aap.api.main:app", "--host", "0.0.0.0", "--port", "8080"]
