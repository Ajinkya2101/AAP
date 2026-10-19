# vLLM image for the RTX 5090 (Blackwell, sm_120).
#
# vLLM 0.10.1 falls back to xformers for the Qwen2.5-VL vision encoder when the
# upstream flash-attn package is missing. On sm_120, xformers dispatches to a
# Hopper-only flash-attention kernel and engine start fails with
#   CUDA error (.../flash-attention/hopper/flash_fwd_launch_template.h:188): invalid argument
# This patch makes that fallback PyTorch SDPA instead (vision encoder only; the
# decoder keeps vLLM's own flash-attention backend). Drop it when the pinned vLLM
# no longer uses xformers for vision.
ARG VLLM_BASE_IMAGE=vllm/vllm-openai:v0.10.1.1
FROM ${VLLM_BASE_IMAGE}
RUN f=$(python3 -c "import vllm.platforms.cuda as m; print(m.__file__)") \
 && grep -q "return _Backend.XFORMERS" "$f" \
 && sed -i 's/        return _Backend.XFORMERS/        return _Backend.TORCH_SDPA/' "$f" \
 && grep -n "return _Backend.TORCH_SDPA" "$f"
