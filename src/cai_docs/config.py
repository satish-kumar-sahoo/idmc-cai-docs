"""Runtime configuration for a cai-docs run."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Config:
    input_path: Path
    output_dir: Path

    # LLM enrichment
    use_llm: bool = True  # only takes effect if an API key is present
    model: str = "claude-opus-4-7"
    max_workers: int = 4
    cache_dir: Path = Path(".cache/cai-docs")

    # classification
    confidence_threshold: float = 0.45  # below this -> needs_review

    # privacy
    include_sample_data: bool = False  # raw sample payloads are PII; off by default

    @property
    def anthropic_api_key(self) -> str | None:
        return os.environ.get("ANTHROPIC_API_KEY") or None

    @property
    def llm_enabled(self) -> bool:
        return self.use_llm and bool(self.anthropic_api_key)
