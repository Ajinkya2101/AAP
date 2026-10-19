"""bge-m3 dense embeddings on the CPU (ONNX Runtime), for GL-history similarity (plan 8).

The model is optional at runtime: `get_embedder()` returns None while the files are
missing (first start: `embed-fetch` is still downloading) or EMBED_ENABLED=false.
Callers then fall back to RapidFuzz, and the periodic backfill fills
gl_history_line.embedding once the model is there.
"""

from __future__ import annotations

import threading
from pathlib import Path

import numpy as np

from aap.config import get_settings
from aap.logging import get_logger

log = get_logger(__name__)
DIM = 1024


class Embedder:
    def __init__(self, model_dir: Path, threads: int = 8):
        import onnxruntime as ort
        from tokenizers import Tokenizer

        onnx_dir = model_dir / "onnx"
        self.tokenizer = Tokenizer.from_file(str(onnx_dir / "tokenizer.json"))
        self.tokenizer.enable_truncation(get_settings().embed_max_tokens)
        self.tokenizer.enable_padding(pad_id=self.tokenizer.token_to_id("<pad>") or 1, pad_token="<pad>")
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = threads
        self.session = ort.InferenceSession(str(onnx_dir / "model.onnx"), opts, providers=["CPUExecutionProvider"])
        self.inputs = {i.name for i in self.session.get_inputs()}
        self.lock = threading.Lock()

    def embed(self, texts: list[str], batch: int = 32) -> np.ndarray:
        out = []
        for i in range(0, len(texts), batch):
            enc = self.tokenizer.encode_batch([t or "" for t in texts[i:i + batch]])
            feed = {"input_ids": np.array([e.ids for e in enc], dtype=np.int64),
                    "attention_mask": np.array([e.attention_mask for e in enc], dtype=np.int64)}
            if "token_type_ids" in self.inputs:
                feed["token_type_ids"] = np.zeros_like(feed["input_ids"])
            with self.lock:
                result = self.session.run(None, {k: v for k, v in feed.items() if k in self.inputs})
            vec = next((r for r in result if r.ndim == 2 and r.shape[1] == DIM), None)
            if vec is None:                       # last_hidden_state -> CLS pooling (bge-m3 dense)
                vec = result[0][:, 0, :]
            out.append(vec / np.linalg.norm(vec, axis=1, keepdims=True).clip(min=1e-12))
        return np.vstack(out) if out else np.zeros((0, DIM), dtype=np.float32)


_lock = threading.Lock()
_embedder: Embedder | None = None
_failed = False


def model_present(model_dir: Path | None = None) -> bool:
    d = model_dir or get_settings().embed_model_dir
    return (d / ".aap-download-complete").exists() and (d / "onnx" / "model.onnx").exists()


def get_embedder() -> Embedder | None:
    global _embedder, _failed
    s = get_settings()
    if not s.embed_enabled or _failed:
        return None
    if _embedder is None:
        if not model_present(s.embed_model_dir):
            return None
        with _lock:
            if _embedder is None:
                try:
                    _embedder = Embedder(s.embed_model_dir, s.embed_threads)
                    log.info("embed.loaded", model=s.embed_model, dir=str(s.embed_model_dir))
                except Exception as exc:  # noqa: BLE001 - fall back to RapidFuzz, do not break the agents
                    _failed = True
                    log.exception("embed.load_failed", error=str(exc))
                    return None
    return _embedder
