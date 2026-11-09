# Training container (docs/PLAN-TRAINING-PIPELINE.md §4): PyTorch with CUDA 12.8 for the RTX 5090 (sm_120).
# Build from the repository root:  docker build -f training/Dockerfile -t aap-train:dev training
# Run (vLLM stopped first):        docker run --rm --gpus all -v %cd%\training:/work -v %cd%\test-data-kk:/test-data-kk ^
#                                    -v hf-cache:/root/.cache/huggingface aap-train:dev ^
#                                    python -m aap_train.train --config configs/smoke-a-qlora.yaml
FROM nvidia/cuda:12.8.1-cudnn-runtime-ubuntu24.04

ENV DEBIAN_FRONTEND=noninteractive PYTHONUNBUFFERED=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH=/opt/venv/bin:$PATH HF_HOME=/root/.cache/huggingface
RUN apt-get update && apt-get install -y --no-install-recommends python3.12 python3.12-venv git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.8.0 /uv /usr/local/bin/uv

WORKDIR /work
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --python /usr/bin/python3.12
COPY src ./src
RUN uv sync --frozen --python /usr/bin/python3.12

CMD ["python", "-m", "aap_train.envcheck"]
