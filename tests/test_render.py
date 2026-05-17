import re
from pathlib import Path

from cai_docs.classify import classify
from cai_docs.config import Config
from cai_docs.describe import describe_assets
from cai_docs.extract import extract
from cai_docs.graph import build_graph
from cai_docs.models import Asset, RunReport
from cai_docs.render import VaultWriter
from cai_docs.xmlmodel import parse


def _asset(raw):
    doc = parse(raw)
    at, conf, sig = classify(doc)
    return extract(doc, at, conf, sig)


def _report(assets):
    rep = RunReport()
    rep.total_assets = len(assets)
    for a in assets:
        rep.counts_by_type[a.asset_type] = rep.counts_by_type.get(a.asset_type, 0) + 1
        bucket = "high" if a.confidence >= 0.75 else "low" if a.confidence < 0.45 else "med"
        rep.confidence_buckets[bucket] = rep.confidence_buckets.get(bucket, 0) + 1
        if a.needs_review:
            rep.needs_review_count += 1
    return rep


def _build_vault(tmp_path, extra=None, include_sample=False):
    assets = [_asset_from(p) for p in ("createMultipleIdentifier", "retrieveconsents")]
    # subprocess stand-ins so wikilinks resolve
    assets.append(Asset(source_relpath="GetConfiguration.PROCESS.xml", asset_type="process",
                         name="GetConfiguration", guid="2Zf1IBmmkiQcZh0NmtUImO",
                         confidence=0.95))
    assets.append(Asset(source_relpath="BuildLocalizedId.PROCESS.xml", asset_type="process",
                         name="BuildLocalizedId", guid="6WAjTPVYl4Pi3XNAg2ZvIo",
                         confidence=0.95))
    if extra:
        assets.extend(extra)
    graph = build_graph(assets)
    cfg = Config(input_path=Path("."), output_dir=tmp_path / "vault",
                 cache_dir=tmp_path / "c", include_sample_data=include_sample,
                 use_llm=False)
    rep = _report(assets)
    describe_assets(assets, graph, cfg, rep)
    VaultWriter(cfg).write(graph, rep)
    return cfg.output_dir


_FX = Path(__file__).parent / "fixtures" / "real"


def _asset_from(stem):
    p = _FX / f"{stem}.PROCESS.xml"
    from cai_docs.models import RawFile

    raw = RawFile(relpath=p.name, abs_path=p, ext="xml", data=p.read_bytes())
    return _asset(raw)


def test_vault_structure_and_pages(tmp_path):
    vault = _build_vault(tmp_path)
    assert (vault / "Home.md").exists()
    assert (vault / "_MOC Processes.md").exists()
    pages = list(vault.rglob("*.md"))
    names = {p.stem for p in pages}
    assert "createMultipleIdentifier" in names
    assert "retrieveconsents" in names
    # mirrored into project folder from PublishedContributionId
    assert (vault / "spi.createMultipleIdentifier" / "createMultipleIdentifier.md").exists()


def test_process_pages_have_mermaid_and_frontmatter(tmp_path):
    vault = _build_vault(tmp_path)
    page = (vault / "spi.createMultipleIdentifier" / "createMultipleIdentifier.md").read_text(
        encoding="utf-8"
    )
    assert page.startswith("---")
    assert 'type: "process"' in page
    assert "```mermaid" in page
    assert "flowchart TD" in page


def test_mermaid_edge_labels_are_quoted(tmp_path):
    """Mermaid rejects unquoted (), $, comma in edge labels -> diagram fails
    to render. Condition expressions (string-equals($temp.x, ...)) MUST be
    emitted as quoted edge labels: A -->|"..."| B."""
    vault = _build_vault(tmp_path)
    page = (vault / "spi.createMultipleIdentifier" / "createMultipleIdentifier.md").read_text(
        encoding="utf-8"
    )
    mermaid = re.search(r"```mermaid\n(.*?)\n```", page, re.DOTALL).group(1)
    edge_label_lines = [
        ln for ln in mermaid.splitlines() if re.search(r"-\.?->\|", ln)
    ]
    assert edge_label_lines, "expected at least one labelled edge"
    for ln in edge_label_lines:
        body = ln.split("|", 1)[1].rsplit("|", 1)[0]
        assert body.startswith('"') and body.endswith('"'), (
            f"edge label not quoted (Mermaid will fail to parse): {ln!r}"
        )


def test_no_dangling_wikilinks(tmp_path):
    vault = _build_vault(tmp_path)
    pages = list(vault.rglob("*.md"))
    note_names = {p.stem for p in pages}
    note_names |= {p.name for p in vault.rglob("*.canvas")}  # canvas is linkable
    link_re = re.compile(r"\[\[([^\]]+)\]\]")
    fence_re = re.compile(r"```.*?```", re.DOTALL)
    for p in pages:
        # Obsidian does not parse wikilinks inside fenced code blocks (e.g. the
        # mermaid `[["label"]]` subroutine shape), so strip fences first.
        text = fence_re.sub("", p.read_text(encoding="utf-8"))
        for m in link_re.finditer(text):
            target = m.group(1).split("|")[0].split("#")[0].strip()
            assert target in note_names, f"dangling [[{target}]] in {p.name}"


def test_shared_subprocess_links_from_both(tmp_path):
    vault = _build_vault(tmp_path)
    create = (vault / "spi.createMultipleIdentifier" / "createMultipleIdentifier.md").read_text(
        "utf-8"
    )
    retrieve = (vault / "spi.retrieveconsents" / "retrieveconsents.md").read_text("utf-8")
    assert "[[GetConfiguration]]" in create
    assert "[[GetConfiguration]]" in retrieve
    assert "[[BuildLocalizedId]]" in create and "[[BuildLocalizedId]]" in retrieve


def test_embedded_sql_rendered(tmp_path):
    vault = _build_vault(tmp_path)
    create = (vault / "spi.createMultipleIdentifier" / "createMultipleIdentifier.md").read_text(
        "utf-8"
    )
    assert "## SQL" in create
    assert "INSERT INTO integration_layer.consent_data_events" in create


def test_sample_data_redacted_by_default(tmp_path):
    vault = _build_vault(tmp_path)
    create = (vault / "spi.createMultipleIdentifier" / "createMultipleIdentifier.md").read_text(
        "utf-8"
    )
    assert "values redacted" in create
    # a known sample payload value must not leak
    assert "kjsdhjgs1009" not in create


def test_sample_data_included_when_opted_in(tmp_path):
    vault = _build_vault(tmp_path, include_sample=True)
    create = (vault / "spi.createMultipleIdentifier" / "createMultipleIdentifier.md").read_text(
        "utf-8"
    )
    assert "kjsdhjgs1009" in create


def test_unknown_tagged_needs_review(tmp_path, synthetic_dir, raw_loader):
    raw = raw_loader(synthetic_dir / "weird_unknown.xml")
    doc = parse(raw)
    at, conf, sig = classify(doc)
    unknown = extract(doc, at, conf, sig)
    vault = _build_vault(tmp_path, extra=[unknown])
    # unknown asset page exists somewhere and carries the needs-review tag
    pages = {p.stem: p for p in vault.rglob("*.md")}
    assert "weird_unknown" in pages
    txt = pages["weird_unknown"].read_text("utf-8")
    assert "needs-review" in txt
    assert "Needs review" in txt
