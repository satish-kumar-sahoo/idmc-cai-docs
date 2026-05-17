"""Command-line entry point: cai-docs <input> -o <vault>."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .auth import LOGIN_HINT
from .config import Config
from .pipeline import run


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="cai-docs",
        description="Generate an Obsidian documentation vault from an "
        "Informatica CAI repository export (.zip or directory).",
    )
    p.add_argument("input", help="path to the CAI export .zip or an extracted directory")
    p.add_argument(
        "-o", "--output", required=True, help="output directory for the Obsidian vault"
    )
    llm = p.add_mutually_exclusive_group()
    llm.add_argument(
        "--llm",
        dest="use_llm",
        action="store_true",
        default=True,
        help="enable Claude prose enrichment when logged in to Claude (default)",
    )
    llm.add_argument(
        "--no-llm",
        dest="use_llm",
        action="store_false",
        help="static extraction only; never call the LLM",
    )
    p.add_argument("--model", default="claude-opus-4-7", help="Anthropic model id")
    p.add_argument("--max-workers", type=int, default=4, help="LLM concurrency")
    p.add_argument(
        "--confidence-threshold",
        type=float,
        default=0.45,
        help="below this, an asset is flagged needs-review",
    )
    p.add_argument(
        "--include-sample-data",
        action="store_true",
        help="emit raw sample payloads (PII) into the vault; off by default",
    )
    p.add_argument("--cache-dir", default=".cache/cai-docs", help="LLM response cache dir")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = Config(
        input_path=Path(args.input),
        output_dir=Path(args.output),
        use_llm=args.use_llm,
        model=args.model,
        max_workers=args.max_workers,
        cache_dir=Path(args.cache_dir),
        confidence_threshold=args.confidence_threshold,
        include_sample_data=args.include_sample_data,
    )
    if config.use_llm and not config.llm_enabled:
        print(f"note: {LOGIN_HINT}", file=sys.stderr)

    try:
        report = run(config)
    except (FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(report.render())
    print(f"\nvault written to: {config.output_dir}")
    if not config.llm_enabled and config.use_llm:
        print("(AI enrichment skipped — not logged in to Claude)")
    if report.mermaid_issues:
        print(
            f"\nwarning: {len(report.mermaid_issues)} Mermaid diagram issue(s) "
            "detected — see the run report above.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
