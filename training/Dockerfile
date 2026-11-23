# Training container (docs/PLAN-TRAINING-PIPELINE.md §4): PyTorch with CUDA 12.8 for the RTX 5090 (sm_120).
# Used through the compose service `train` (profile "train", docker-compose.yml) and train.bat at the
# repository root, which mount the repository at /repo, run in /repo/training with the live source
# (PYTHONPATH), and read the base weights from the stack's `models` volume. Build: train.bat build
FROM nvidia/cuda:12.8.1-cudnn-runtime-ubuntu24.04

ENV DEBIAN_FRONTEND=noninteractive PYTHONUNBUFFERED=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH=/opt/venv/bin:$PATH HF_HOME=/root/.cache/huggingface
# IPv4 first: Docker Desktop's build network has no IPv6 route, and download-r2.pytorch.org (the torch
# wheels) also resolves to IPv6, which failed the build with "Network unreachable" (2026-11-23)
RUN echo "precedence ::ffff:0:0/96  100" >> /etc/gai.conf
# git: run.json records the commit; the bind-mounted repository belongs to another user (safe.directory)
RUN apt-get update && apt-get install -y --no-install-recommends python3.12 python3.12-venv git ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && git config --system --add safe.directory '*'
COPY --from=ghcr.io/astral-sh/uv:0.8.0 /uv /usr/local/bin/uv

WORKDIR /work
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --python /usr/bin/python3.12
COPY src ./src
RUN uv sync --frozen --python /usr/bin/python3.12

CMD ["python", "-m", "aap_train.envcheck"]
