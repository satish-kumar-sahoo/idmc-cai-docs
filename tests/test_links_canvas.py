"""Reference filtering/resolution, external stubs, and Obsidian Canvas."""

import json
from pathlib import Path

from cai_docs.classify import classify
from cai_docs.config import Config
from cai_docs.extract import _is_platform_ref, extract
from cai_docs.graph import build_graph
from cai_docs.models import Asset, Reference
from cai_docs.pipeline import run
from cai_docs.xmlmodel import parse

SCM = Path(__file__).parent / "fixtures" / "scm"


def test_platform_refs_filtered(real_retrieve):
    doc = parse(real_retrieve)
    at, conf, sig = classify(doc)
    a = extract(doc, at, conf, sig)
    raws = [r.raw for r in a.references if r.kind == "catalog_resource"]
    assert any("purposes.xml" in r for r in raws)  # real project resource kept
    assert not any(_is_platform_ref(r) for r in raws)  # engine namespaces dropped


def test_catalog_path_resolves_by_basename():
    proc = Asset(source_relpath="P.PROCESS.xml", asset_type="process", name="P",
                 guid="P1")
    proc.references = [
        Reference(kind="catalog_resource",
                  raw="project:/SaaSGlobal/metadata/purposes.xml",
                  target_name="project:/SaaSGlobal/metadata/purposes.xml")
    ]
    res = Asset(source_relpath="SaasGlobal/metadata/purposes.xml",
                asset_type="resource", name="purposes", guid="R1")
    g = build_graph([proc, res])
    assert any(e.resolved and e.target_key == res.key for e in g.uses[proc.key])


def test_connector_name_resolves_with_prefix():
    proc = Asset(source_relpath="P.PROCESS.xml", asset_type="process", name="P",
                 guid="P1")
    proc.references = [Reference(kind="connection", raw="OT-Login",
                                 target_name="OT-Login")]
    conn = Asset(source_relpath="AppConnection-OT-Login.AI_CONNECTION.xml",
                 asset_type="connection", name="AppConnection-OT-Login",
                 guid="C1")
    g = build_graph([proc, conn])
    assert any(e.resolved and e.target_key == conn.key for e in g.uses[proc.key])


def test_canvas_and_external_stub(tmp_path):
    cfg = Config(input_path=SCM, output_dir=tmp_path / "v",
                 cache_dir=tmp_path / "c", use_llm=False)
    run(cfg)
    vault = tmp_path / "v"

    canvas = vault / "_Dependencies.canvas"
    assert canvas.exists()
    data = json.loads(canvas.read_text(encoding="utf-8"))
    assert data["nodes"] and "edges" in data
    # file nodes must point at notes that exist
    for n in data["nodes"]:
        if n.get("type") == "file":
            assert (vault / n["file"]).exists(), n["file"]
    # edges reference declared node ids
    ids = {n["id"] for n in data["nodes"]}
    for e in data["edges"]:
        assert e["fromNode"] in ids and e["toNode"] in ids

    # external/unresolved deps become linkable stub notes
    ext = list((vault / "_External").glob("*.md")) if (vault / "_External").exists() else []
    assert ext, "expected external stub notes for unresolved deps"
    stub = ext[0].read_text(encoding="utf-8")
    assert "External / unresolved reference" in stub
    assert "## Referenced by" in stub

    # Uses sections never emit raw '(unresolved)' plain text anymore
    for p in vault.rglob("*.md"):
        assert "_(unresolved)_" not in p.read_text(encoding="utf-8")
