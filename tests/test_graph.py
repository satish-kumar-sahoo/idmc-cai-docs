from cai_docs.classify import classify
from cai_docs.extract import extract
from cai_docs.graph import build_graph
from cai_docs.models import Asset
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
