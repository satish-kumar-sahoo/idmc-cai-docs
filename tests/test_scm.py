"""IDMC source-control format: sidecars, businesssconnector mime, BPEL, noise."""

from pathlib import Path

from cai_docs.classify import classify
from cai_docs.config import Config
from cai_docs.extract import extract
from cai_docs.ingest import discover
from cai_docs.pipeline import run
from cai_docs.sidecar import normalize_object_info, pair_sidecars
from cai_docs.xmlmodel import parse

SCM = Path(__file__).parent / "fixtures" / "scm"


def _classify_extract(rf, sidecar=None):
    doc = parse(rf)
    at, conf, sig = classify(doc)
    a = extract(doc, at, conf, sig)
    if sidecar:
        from cai_docs.sidecar import apply_sidecar

        apply_sidecar(a, sidecar)
    return a


def test_ingest_drops_noise_keeps_assets_and_sidecars():
    files = discover(SCM)
    rels = {f.relpath for f in files}
    assert not any(r.endswith((".jpg", ".yml", ".project")) for r in rels)
    assert ".github/workflows/ci.yml" not in rels
    assert any(r.endswith("AppConnection-DASSQLServer.AI_CONNECTION.xml") for r in rels)
    assert any(r.endswith(".AppConnection-DASSQLServer.AI_CONNECTION.json") for r in rels)
    assert any(r.endswith("SaveConfig.bpel") for r in rels)


def test_sidecar_pairing_removes_standalone_and_maps_metadata():
    files = discover(SCM)
    assets, meta = pair_sidecars(files)
    arels = {f.relpath for f in assets}
    # paired sidecars are gone from the asset list
    assert not any(
        r.endswith(".AppConnection-DASSQLServer.AI_CONNECTION.json") for r in arels
    )
    # primary is mapped to its objectInfo
    key = next(r for r in meta if r.endswith("AppConnection-DASSQLServer.AI_CONNECTION.xml"))
    assert meta[key]["id"] == "3fQumCWzh3lhEYusyDt3aT"
    assert meta[key]["_cai_type"] == "connection"
    # folder/project descriptors are scaffolding, dropped at ingest
    assert not any(r.endswith(".Project.json") for r in arels)


def test_sidecar_guid_is_authoritative():
    files = discover(SCM)
    assets, meta = pair_sidecars(files)
    conn_rf = next(
        f for f in assets if f.relpath.endswith("AppConnection-DASSQLServer.AI_CONNECTION.xml")
    )
    a = _classify_extract(conn_rf, meta[conn_rf.relpath])
    assert a.asset_type == "connection"
    assert a.guid == "3fQumCWzh3lhEYusyDt3aT"  # from sidecar, not the XML placeholder
    assert a.description  # populated (XML or sidecar)
    assert not a.needs_review


def test_service_connector_businesssconnector_mime(raw_loader):
    rf = raw_loader(
        SCM / "Explore/Proj/Service_Connectors/DASConnectorSQLServer.AI_SERVICE_CONNECTOR.xml"
    )
    at, conf, sig = classify(parse(rf))
    assert at == "service_connector", sig
    assert conf > 0.45


def test_bpel_classified_and_refs(raw_loader):
    rf = raw_loader(SCM / "Explore/SaasGlobal/bpel/SaveConfig.bpel")
    a = _classify_extract(rf)
    assert a.asset_type == "process", a.classification_signals
    kinds = {r.context for r in a.references}
    assert any("partnerLink" in (c or "") for c in kinds)
    assert any("import" in (c or "") for c in kinds)
    assert a.rest_trigger is True  # receive createInstance="yes"


def test_pdd_is_deployment(raw_loader):
    rf = raw_loader(SCM / "Explore/SaasGlobal/deploy/SaveConfig.pdd")
    at, _, sig = classify(parse(rf))
    assert at == "deployment", sig


def test_full_pipeline_scm_no_unknown_noise(tmp_path):
    cfg = Config(input_path=SCM, output_dir=tmp_path / "v",
                 cache_dir=tmp_path / "c", use_llm=False)
    rep = run(cfg)
    # everything classified; no unknowns
    assert rep.counts_by_type.get("unknown", 0) == 0
    assert rep.counts_by_type.get("connection", 0) == 1
    assert rep.counts_by_type.get("service_connector", 0) == 1
    assert rep.counts_by_type.get("process", 0) == 1  # the .bpel
    # the .pdd is merged into its .bpel process, not a standalone asset
    assert rep.counts_by_type.get("deployment", 0) == 0
    # no jpg/.project pages, no standalone sidecar pages
    stems = {p.stem for p in (tmp_path / "v").rglob("*.md")}
    assert "flow" not in stems
    assert not any(s.startswith(".AppConnection") for s in stems)
    # the merged deployment descriptor surfaces on the process page
    bpel_md = next(p for p in (tmp_path / "v").rglob("SaveConfig*.md"))
    assert "deployment descriptor merged" in bpel_md.read_text(encoding="utf-8")
