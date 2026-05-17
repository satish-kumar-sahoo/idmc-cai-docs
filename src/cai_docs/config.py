"""Runtime configuration for a cai-docs run."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .auth import ClaudeAuth, resolve_auth


@dataclass
class Config:
    input_path: Path
    output_dir: Path

    # LLM enrichment
    use_llm: bool = True  # only takes effect if the user is logged in to Claude
    model: str = "claude-opus-4-7"
    max_workers: int = 4
    cache_dir: Path = Path(".cache/cai-docs")

    # classification
    confidence_threshold: float = 0.45  # below this -> needs_review

    # privacy
    include_sample_data: bool = False  # raw sample payloads are PII; off by default

    @property
    def auth(self) -> ClaudeAuth | None:
        return resolve_auth()

    @property
    def llm_enabled(self) -> bool:
        return self.use_llm and self.auth is not None
