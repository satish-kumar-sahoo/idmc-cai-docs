"""Stage 6: describe each asset.

Static, deterministic summary is always produced. When an Anthropic API key
is present and LLM use is enabled, a short prose narrative is added on top,
content-hash cached so re-runs are cheap and deterministic. Secrets and
sample-data payloads are never sent to the model.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter

from .config import Config
from .models import Asset, AssetGraph, RunReport

_SECRET_HINT = ("secret", "password", "passwd", "token", "apikey", "api_key", "credential")


def static_summary(asset: Asset, graph: AssetGraph) -> str:
    lines: list[str] = []
    kind = asset.asset_type.replace("_", " ")
    lead = f"**{asset.name}** is a {kind}"
    if asset.description:
        lead += f" — {asset.description.strip()}"
    lines.append(lead + ".")

    if asset.asset_type == "process" and asset.flow:
        kinds = Counter(n.kind for n in asset.flow.nodes)
        bits = []
        for k in ("subflow", "service", "assignment", "container", "throw"):
            if kinds.get(k):
                label = {
                    "subflow": "subprocess call",
                    "service": "connector call",
                    "assignment": "assignment",
                    "container": "branch/parallel block",
                    "throw": "error",
                }[k]
                n = kinds[k]
                bits.append(f"{n} {label}{'s' if n != 1 else ''}")
        if bits:
            lines.append("Flow contains " + ", ".join(bits) + ".")
        if asset.rest_trigger:
            lines.append("Triggered as a REST endpoint.")

    if asset.inputs or asset.outputs:
        lines.append(
            f"Interface: {len(asset.inputs)} input(s), {len(asset.outputs)} output(s), "
            f"{len(asset.temp_fields)} temp field(s)."
        )

    subs = [r.target_name for r in asset.references if r.kind == "subprocess"]
    if subs:
        lines.append("Calls subprocesses: " + ", ".join(sorted(set(filter(None, subs)))) + ".")
    conns = sorted(
        {
            f"{r.target_name}{(' / ' + r.action) if r.action else ''}"
            for r in asset.references
            if r.kind in ("connection", "service_connector")
        }
    )
    if conns:
        lines.append("Uses connections: " + "; ".join(conns) + ".")
    if asset.sql_blocks:
        lines.append(f"Executes {len(asset.sql_blocks)} embedded SQL statement(s).")
    cats = sorted({r.raw for r in asset.references if r.kind == "catalog_resource"})
    if cats:
        lines.append("Reads project resources: " + ", ".join(cats) + ".")

    used_by = graph.used_by.get(asset.key, [])
    if used_by:
        lines.append(f"Used by {len({e.source_key for e in used_by})} other asset(s).")
    return "\n".join(lines)


def _redacted_payload(asset: Asset) -> dict:
    """Compact, secret-free view sent to the LLM. No sample data, no secrets."""

    def safe_config(cfg: dict[str, str]) -> dict[str, str]:
        return {
            k: ("<redacted>" if any(h in k.lower() for h in _SECRET_HINT) else v)
            for k, v in cfg.items()
        }

    return {
        "name": asset.name,
        "type": asset.asset_type,
        "description": asset.description,
        "inputs": [f.name for f in asset.inputs],
        "outputs": [f.name for f in asset.outputs],
        "temp_fields": [f.name for f in asset.temp_fields],
        "subprocess_calls": sorted(
            {r.target_name for r in asset.references if r.kind == "subprocess" and r.target_name}
        ),
        "connector_calls": sorted(
            {
                f"{r.target_name}:{r.action}"
                for r in asset.references
                if r.kind in ("connection", "service_connector")
            }
        ),
        "sql": [b.reconstructed or b.raw_expression for b in asset.sql_blocks][:5],
        "expressions": [e.expression for e in asset.expressions][:8],
        "rest_trigger": asset.rest_trigger,
        "config": safe_config(asset.config),
    }


def _prompt(asset: Asset) -> str:
    payload = json.dumps(_redacted_payload(asset), indent=2, ensure_ascii=False)
    return (
        "You are documenting an Informatica Cloud Application Integration asset "
        "for engineers browsing an Obsidian knowledge base. Given the structured "
        "summary below, write 2-4 sentences of plain-English prose explaining what "
        "this asset does and its core logic. Be concrete and factual; do not invent "
        "details that are not implied by the data. Do not use headings or lists.\n\n"
        f"{payload}"
    )


def _cache_key(model: str, prompt: str) -> str:
    return hashlib.sha256(f"{model}\n{prompt}".encode()).hexdigest()


def _call_llm(prompt: str, config: Config) -> str:
    """Isolated so tests can monkeypatch it. Real Anthropic call otherwise."""
    import anthropic  # lazy: optional dependency

    client = anthropic.Anthropic(api_key=config.anthropic_api_key)
    resp = client.messages.create(
        model=config.model,
        max_tokens=400,
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(
        block.text for block in resp.content if getattr(block, "type", "") == "text"
    ).strip()


def _enrich(asset: Asset, config: Config, report: RunReport) -> None:
    prompt = _prompt(asset)
    key = _cache_key(config.model, prompt)
    cache_file = config.cache_dir / f"{key}.txt"
    if cache_file.exists():
        asset.llm_narrative = cache_file.read_text(encoding="utf-8")
        report.llm_cache_hits += 1
        return
    try:
        text = _call_llm(prompt, config)
    except Exception as exc:  # never let LLM failure break the run
        asset.notes.append(f"llm enrichment skipped: {type(exc).__name__}: {exc}")
        return
    asset.llm_narrative = text
    report.llm_calls += 1
    try:
        config.cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(text, encoding="utf-8")
    except OSError:
        pass


def describe_assets(
    assets: list[Asset], graph: AssetGraph, config: Config, report: RunReport
) -> None:
    for a in assets:
        a.static_summary = static_summary(a, graph)
    if not config.llm_enabled:
        return
    for a in assets:
        _enrich(a, config, report)
