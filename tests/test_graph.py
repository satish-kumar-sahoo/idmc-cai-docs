from cai_docs.classify import classify
from cai_docs.extract import extract
from cai_docs.graph import build_graph
from cai_docs.models import Asset, Reference
from cai_docs.xmlmodel import parse


def _asset(raw):
    doc = parse(raw)
    at, conf, sig = classify(doc)
    return extract(doc, at, conf, sig)


def test_shared_subprocess_resolves_across_processes(real_create, real_retrieve):
    create = _asset(real_create)
    retrieve = _asset(real_retrieve)

    # Stand-in subprocess assets the two real processes both call (by GUID).
    get_config = Asset(source_relpath="GetConfiguration.PROCESS.xml", asset_type="process",
                        name="GetConfiguration", guid="2Zf1IBmmkiQcZh0NmtUImO")
    build_local = Asset(source_relpath="BuildLocalizedId.PROCESS.xml", asset_type="process",
                        name="BuildLocalizedId", guid="6WAjTPVYl4Pi3XNAg2ZvIo")

    g = build_graph([create, retrieve, get_config, build_local])

    # Both processes link to the SAME GetConfiguration asset (no duplication).
    users = {e.source_key for e in g.used_by.get(get_config.key, [])}
    assert create.key in users
    assert retrieve.key in users

    # used_by for BuildLocalizedId includes both as well
    bl_users = {e.source_key for e in g.used_by.get(build_local.key, [])}
    assert {create.key, retrieve.key} <= bl_users


def test_connection_reference_resolves_by_name(real_create):
    create = _asset(real_create)
    conn = Asset(source_relpath="OT-Submit-Consent.CONNECTION.xml",
                 asset_type="connection", name="OT-Submit-Consent",
                 guid="CONNGUID00000000000001")
    g = build_graph([create, conn])
    edges = g.used_by.get(conn.key, [])
    assert any(e.kind == "uses-connection" for e in edges)


def test_unresolved_becomes_external(real_create):
    create = _asset(real_create)
    g = build_graph([create])  # nothing to resolve against
    assert g.unresolved
    assert any(not e.resolved and e.target_key.startswith("external:") for e in g.edges)
    # the process still has outgoing 'uses' edges recorded
    assert g.uses.get(create.key)


def test_catalog_resource_edge_carries_raw_path_resolved(real_retrieve):
    """retrieveconsents references project:/SaaSGlobal/metadata/purposes.xml via
    getCatalogResource. The resulting edge must keep that raw path so the
    renderer can display it under Deployed Resources."""
    retrieve = _asset(real_retrieve)
    purposes = Asset(
        source_relpath="SaaSGlobal/metadata/purposes.xml",
        asset_type="resource",
        name="purposes",
        guid="RESGUID00000000000001",
    )
    g = build_graph([retrieve, purposes])
    res_edges = [
        e for e in g.uses.get(retrieve.key, [])
        if e.kind == "references-resource"
    ]
    assert res_edges, "expected at least one references-resource edge"
    assert any(
        e.resolved and e.raw_target == "project:/SaaSGlobal/metadata/purposes.xml"
        for e in res_edges
    )


def test_catalog_resource_two_imports_to_same_asset_keep_distinct_edges():
    """A BPEL process can import both saasGlobal.xsd and saasGlobal.wsdl — they
    resolve to the same `saasGlobal` resource asset but are distinct deployed
    XML files, so both must appear as separate edges (and separate
    Deployed-Resources rows). Dedup-by-(src,target,kind) was dropping the
    second one."""
    proc = Asset(
        source_relpath="SaveCollectionPoints.bpel",
        asset_type="process",
        name="SaveCollectionPoints",
        guid="PROCGUID0000000000001",
        references=[
            Reference(
                kind="catalog_resource",
                raw="../../SaaSGlobalVariables/schema/saasGlobal.xsd",
                target_name="../../SaaSGlobalVariables/schema/saasGlobal.xsd",
                context="bpel import (xsd)",
            ),
            Reference(
                kind="catalog_resource",
                raw="../../SaaSGlobalVariables/wsdl/saasGlobal.wsdl",
                target_name="../../SaaSGlobalVariables/wsdl/saasGlobal.wsdl",
                context="bpel import (wsdl)",
            ),
        ],
    )
    saas = Asset(
        source_relpath="SaaSGlobalVariables/wsdl/saasGlobal.wsdl",
        asset_type="resource",
        name="saasGlobal",
        guid="RESGUID00000000000002",
    )
    g = build_graph([proc, saas])
    res_edges = [
        e for e in g.uses.get(proc.key, [])
        if e.kind == "references-resource"
    ]
    raws = {e.raw_target for e in res_edges}
    assert raws == {
        "../../SaaSGlobalVariables/schema/saasGlobal.xsd",
        "../../SaaSGlobalVariables/wsdl/saasGlobal.wsdl",
    }, f"expected both imports as distinct edges, got {raws}"


def test_catalog_resource_edge_carries_raw_path_unresolved(real_retrieve):
    retrieve = _asset(real_retrieve)
    g = build_graph([retrieve])  # no purposes asset → unresolved
    res_edges = [
        e for e in g.uses.get(retrieve.key, [])
        if e.kind == "references-resource"
    ]
    assert any(
        not e.resolved and e.raw_target == "project:/SaaSGlobal/metadata/purposes.xml"
        for e in res_edges
    )
