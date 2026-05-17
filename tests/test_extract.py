from cai_docs.classify import PROCESS, classify
from cai_docs.extract import extract
from cai_docs.xmlmodel import parse


def _asset(raw):
    doc = parse(raw)
    at, conf, sig = classify(doc)
    return extract(doc, at, conf, sig)


def test_create_identity_and_metadata(real_create):
    a = _asset(real_create)
    assert a.asset_type == PROCESS
    assert a.name == "createMultipleIdentifier"
    assert a.guid == "7JToW2cvNHhkqxsrxQt7mT"
    assert a.created_by == "rohit.varshney@external.toyota-europe.com"
    assert a.publication_status == "published"
    assert a.project_path == "spi.createMultipleIdentifier"
    assert a.rest_trigger is True
    assert not a.needs_review


def test_create_interface(real_create):
    a = _asset(real_create)
    in_names = {f.name for f in a.inputs}
    assert {"requestID", "nmscCode", "countryCode", "customerID"} <= in_names
    assert {f.name for f in a.outputs} == {"output"}
    temp = {f.name: f for f in a.temp_fields}
    assert temp["temp_SC_Name"].initial_value == "ServiceConnector-OT-Submit-Consent"


def test_create_references(real_create):
    a = _asset(real_create)
    sub = {r.target_name for r in a.references if r.kind == "subprocess"}
    assert {"GetConfiguration", "GetMDMIDProcess", "GetCollectionPoint", "BuildLocalizedId"} <= sub
    conns = {(r.target_name, r.action) for r in a.references if r.kind == "connection"}
    assert ("OT-Submit-Consent", "Create Multiple Identifiers") in conns
    # the DAS SQL connection is referenced as a service too
    assert any("createmultipleidentifier" in (r.target_name or "") for r in a.references)


def test_create_embedded_sql(real_create):
    a = _asset(real_create)
    assert a.sql_blocks
    blk = a.sql_blocks[0]
    assert "INSERT INTO integration_layer.consent_data_events" in blk.raw_expression
    assert blk.reconstructed and "INSERT INTO" in blk.reconstructed


def test_create_flow_graph(real_create):
    a = _asset(real_create)
    assert a.flow is not None
    kinds = {n.kind for n in a.flow.nodes}
    assert {"start", "end", "assignment", "service", "subflow", "container"} <= kinds
    assert a.flow.start_id
    assert a.flow.end_ids
    assert a.flow.edges
    # at least one decision container
    assert any(n.kind == "container" and n.attrs.get("type") == "exclusive" for n in a.flow.nodes)


def test_create_sample_data_parsed(real_create):
    a = _asset(real_create)
    assert len(a.sample_data) >= 5
    sd = a.sample_data[0]
    assert "nmscCode" in sd.field_keys
    assert sd.raw_json  # retained in-model; render redacts unless opted in


def test_retrieve_parallel_and_catalog(real_retrieve):
    a = _asset(real_retrieve)
    assert a.guid == "1bZ0jEJviAilDoJp16qHor"
    assert any(
        n.kind == "container" and n.attrs.get("type") == "parallel" for n in a.flow.nodes
    )
    cats = {r.raw for r in a.references if r.kind == "catalog_resource"}
    assert any("purposes.xml" in c for c in cats)
    # shared subprocess GUIDs appear here too (used by graph stage)
    guids = {r.target_guid for r in a.references if r.kind == "subprocess"}
    assert "2Zf1IBmmkiQcZh0NmtUImO" in guids  # GetConfiguration
    assert "6WAjTPVYl4Pi3XNAg2ZvIo" in guids  # BuildLocalizedId


def test_unknown_still_produces_asset(synthetic_dir, raw_loader):
    raw = raw_loader(synthetic_dir / "weird_unknown.xml")
    doc = parse(raw)
    at, conf, sig = classify(doc)
    a = extract(doc, at, conf, sig)
    assert a.asset_type == "unknown"
    assert a.needs_review is True
    assert a.raw_dump  # nothing-is-lost fallback populated
