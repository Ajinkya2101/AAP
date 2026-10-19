"""One-shot model download into the `models` volume (compose service model-fetch).

Downloads only when the target directory has no config.json, so restarts are
offline-safe (P-01: after the first download no internet access is needed).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from aap.config import get_settings

MODELS_DIR = Path(os.environ.get("MODELS_DIR", "/models"))


MARKER = ".aap-download-complete"


LLM_FILES = ["*.json", "*.safetensors", "*.txt", "*.model", "*.tiktoken", "*.jinja", "merges.txt", "vocab.*"]
# bge-m3 runs on the CPU through ONNX Runtime (aap.embed): only the ONNX export is needed.
EMBED_FILES = ["onnx/*"]


def fetch(repo_id: str, target: Path, patterns: list[str] | None = None) -> None:
    # A marker, not config.json: an interrupted download leaves config.json behind.
    if (target / MARKER).exists():
        print(f"[model-fetch] {repo_id}: present at {target}")
        return
    from huggingface_hub import snapshot_download

    print(f"[model-fetch] downloading {repo_id} -> {target} (first start only)", flush=True)
    snapshot_download(repo_id=repo_id, local_dir=str(target), token=os.environ.get("HF_TOKEN") or None,
                      allow_patterns=patterns or LLM_FILES)
    (target / MARKER).write_text(repo_id)
    print(f"[model-fetch] {repo_id}: done", flush=True)


def main() -> int:
    s = get_settings()
    if "--embed" in sys.argv[1:]:
        fetch(s.embed_model, s.embed_model_dir, EMBED_FILES)
        return 0
    wanted = [s.extract_model_repo]
    if os.environ.get("COMPOSE_PROFILES", "").find("gpu2") >= 0 or os.environ.get("FETCH_CHOICE_MODEL") == "1":
        wanted.append(s.choice_model_repo)
    for repo in wanted:
        fetch(repo, MODELS_DIR / repo.split("/")[-1])
    return 0


if __name__ == "__main__":
    sys.exit(main())
