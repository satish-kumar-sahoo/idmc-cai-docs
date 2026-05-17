"""Orchestrates the full pipeline: ingest -> parse -> classify -> extract ->
graph -> describe -> render. Returns a RunReport."""

from __future__ import annotations

from .classify import classify
from .config import Config
from .describe import describe_assets
from .extract import extract
from .graph import build_graph
from .ingest import discover
from .models import RunReport
from .render import VaultWriter
from .sidecar import apply_sidecar, normalize_object_info, pair_sidecars
from .xmlmodel import parse


def _bucket(conf: float, threshold: float) -> str:
    if conf >= 0.75:
        return "high"
    if conf >= threshold:
        return "medium"
    return "low"


def run(config: Config) -> RunReport:
    report = RunReport()

    raw_files = discover(config.input_path)
    report.files_seen = len(raw_files)

    asset_files, sidecar_meta = pair_sidecars(raw_files)

    assets = []
    for raw in asset_files:
        doc = parse(raw)
        if doc.parse_error is None and (doc.tree is not None or doc.json_sidecar is not None):
            report.files_parsed += 1
        atype, conf, signals = classify(doc)
        asset = extract(doc, atype, conf, signals, config.confidence_threshold)

        info = sidecar_meta.get(raw.relpath)
        if info is None and doc.json_sidecar is not None:
            info = normalize_object_info(doc.raw_text)  # unpaired sidecar
        if info:
            apply_sidecar(asset, info, config.confidence_threshold)

        assets.append(asset)

    graph = build_graph(assets)

    report.total_assets = len(assets)
    for a in assets:
        report.counts_by_type[a.asset_type] = report.counts_by_type.get(a.asset_type, 0) + 1
        b = _bucket(a.confidence, config.confidence_threshold)
        report.confidence_buckets[b] = report.confidence_buckets.get(b, 0) + 1
        if a.needs_review:
            report.needs_review_count += 1
    report.unresolved_references = len(graph.unresolved)

    describe_assets(assets, graph, config, report)
    VaultWriter(config).write(graph, report)
    return report
