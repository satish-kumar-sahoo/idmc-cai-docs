"""Stage 6: describe each asset.

Static, deterministic summary is always produced. When an Anthropic API key
is present and LLM use is enabled, a short prose narrative is added on top,
content-hash cached so re-runs are cheap and deterministic. Secrets and
sample-data payloads are never sent to the model.
"""

from __future__ import annotations

import hashlib
import json

from .config import Config
from .models import Asset, AssetGraph, RunReport

_SECRET_HINT = ("secret", "password", "passwd", "token", "apikey", "api_key", "credential")


def _join(names: list[str], limit: int = 6) -> str:
    names = [n for n in names if n]
    if len(names) > limit:
        return ", ".join(names[:limit]) + f", and {len(names) - limit} more"
    if len(names) > 1:
        return ", ".join(names[:-1]) + " and " + names[-1]
    return names[0] if names else ""


def static_summary(asset: Asset, graph: AssetGraph) -> str:
    """A short, functional description — what the asset does, in plain terms."""
    lines: list[str] = []
    kind = asset.asset_type.replace("_", " ")
    if asset.description:
        lines.append(asset.description.strip())
    else:
        lines.append(f"{asset.name} is a {kind}.")

    if asset.asset_type == "process":
        subs = sorted({r.target_name for r in asset.references if r.kind == "subprocess"})
        conns = sorted(
            {
                r.target_name
                for r in asset.references
                if r.kind in ("connection", "service_connector")
            }
        )
        does: list[str] = []
        if asset.rest_trigger:
            does.append("is triggered as a REST endpoint")
        if subs:
            does.append(f"orchestrates {_join(list(filter(None, subs)))}")
        if conns:
            does.append(f"integrates with {_join(list(filter(None, conns)))}")
        if asset.sql_blocks:
            n = len(asset.sql_blocks)
            does.append(f"runs {n} database quer{'y' if n == 1 else 'ies'}")
        if does:
            lines.append(
                f"It {_join(does, limit=4)}." if len(does) > 1 else f"It {does[0]}."
            )
    elif asset.asset_type in ("connection", "service_connector"):
        if asset.connector_actions:
            acts = [a.label or a.name for a in asset.connector_actions]
            lines.append(f"Exposes {len(acts)} action(s): {_join(acts)}.")
        if asset.inputs:
            lines.append(f"Configured by {len(asset.inputs)} parameter(s).")

    used_by = graph.used_by.get(asset.key, [])
    if used_by:
        by_key = graph.by_key()
        callers = sorted(
            {
                (by_key[e.source_key].name if e.source_key in by_key else e.source_key)
                for e in used_by
            }
        )
        lines.append(f"Used by {_join(callers)}.")
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
        "for someone who wants to understand what it does, not how it is wired. "
        "Given the structured summary below, write 2-3 sentences of plain-English "
        "prose describing its business purpose and what it accomplishes — the "
        "outcome and the data it works with. Favour functional language over "
        "technical internals; do not list node types, parameter counts, or "
        "implementation mechanics. Be factual; do not invent details. No "
        "headings or lists.\n\n"
        f"{payload}"
    )


def _cache_key(model: str, prompt: str) -> str:
    return hashlib.sha256(f"{model}\n{prompt}".encode()).hexdigest()


def _call_llm(prompt: str, config: Config) -> str:
    """Isolated so tests can monkeypatch it. Real Anthropic call otherwise."""
    import anthropic  # lazy: optional dependency

    auth = config.auth
    if auth is None:
        raise RuntimeError("not authenticated")
    if auth.kind == "oauth":
        client = anthropic.Anthropic(auth_token=auth.token)
    else:
        client = anthropic.Anthropic(api_key=auth.token)
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
