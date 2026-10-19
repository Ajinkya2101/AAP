"""Typed settings from the environment / .env (plan section 14)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    # runtime switches
    adapter: Literal["ebs_mock"] = "ebs_mock"
    llm_backend: Literal["vllm", "stub"] = "stub"
    dev_reload: bool = False
    log_level: str = "INFO"

    # databases
    aap_dsn: str = "postgresql://aap_app:aap_app@localhost:5432/aap"
    aap_owner_dsn: str = "postgresql://aap_owner:aap_owner@localhost:5432/aap"
    ebs_owner_dsn: str = "postgresql://ebs_owner:ebs_owner@localhost:5432/ebs"
    ebs_ro_dsn: str = "postgresql://apps_ro:apps_ro@localhost:5432/ebs"
    ebs_intf_dsn: str = "postgresql://apps_intf:apps_intf@localhost:5432/ebs"

    # mock EBS
    seed: int = 42
    seed_scale: Literal["small", "default", "large"] = "default"
    seed_anchor_date: date = date(2026, 9, 28)   # business "today" of the mock EBS data
    ebs_import_delay_min: float = 1.0
    ebs_import_delay_max: float = 10.0

    # LLM
    vllm_base_url: str = "http://vllm:8000/v1"
    vllm_api_key: str = "local"
    extract_model: str = "qwen2.5-vl-7b"
    choice_model: str = "qwen2.5-vl-7b"
    extract_model_repo: str = "Qwen/Qwen2.5-VL-7B-Instruct"
    choice_model_repo: str = "Qwen/Qwen3-8B"
    embed_model: str = "BAAI/bge-m3"
    embed_model_dir: Path = Path("/models/bge-m3")   # ONNX export (onnx/model.onnx + tokenizer.json)
    embed_enabled: bool = True                       # False: GL similarity uses RapidFuzz only
    embed_threads: int = 8
    llm_timeout_seconds: float = 180.0
    llm_max_images_per_request: int = 4     # also vLLM's --limit-mm-per-prompt (docker-compose.yml)
    # Generation. Temperature 0 = the same answer for the same document (and what the eval gates assume).
    llm_temperature: float = 0.0
    extract_max_tokens: int = 4096          # per extraction request; an answer cut off here fails the document
    choice_max_tokens: int = 300            # per choice request (mapping, line pairing, GL coding)
    extract_max_text_chars_per_page: int = 12000   # text layer sent with a page; the rest is dropped
    page_render_dpi: int = 200              # PDF page images for the vision model
    embed_max_tokens: int = 128             # invoice line texts are short

    # files
    filestore_dir: Path = Path("/data/filestore")
    truth_dir: Path | None = None
    max_upload_mb: int = 25
    # Testing only: a manual upload (UI or API) of a file that was already uploaded makes a new
    # document and runs the pipeline again instead of pointing to the first one. Keep False in prod.
    allow_duplicate_uploads: bool = False

    # agents
    match_auto_accept: float = 0.80
    match_margin: float = 0.15
    gl_sim_auto: float = 0.90
    # Exact-history GL: take the account without review when this share of at least
    # GL_EXACT_MIN past lines with the same text agree (history contains miscodings, plan 3.5).
    gl_exact_auto: float = 0.90
    gl_exact_min: int = 3

    # pipeline
    invoice_source: str = "AAP"
    text_quality_threshold: float = 0.60
    invoice_max_age_days: int = 365
    amount_tolerance: Decimal = Field(default=Decimal("0.01"))

    @property
    def truth_path(self) -> Path:
        return self.truth_dir or (self.filestore_dir / "truth")


@lru_cache
def get_settings() -> Settings:
    return Settings()
