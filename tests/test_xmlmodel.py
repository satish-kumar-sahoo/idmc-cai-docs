from cai_docs.xmlmodel import (
    children_local,
    descendants_local,
    first_text,
    lname,
    parse,
)


def test_parse_real_process(real_create):
    doc = parse(real_create)
    assert doc.parse_error is None
    assert doc.tree is not None
    assert doc.root_localname == "getResponse"
    # avrepository namespace is present on the root
    assert any("avrepository" in uri for uri in doc.namespaces.values())


def test_namespace_agnostic_helpers(real_retrieve):
    doc = parse(real_retrieve)
    item = children_local(doc.tree, "Item")[0]
    assert first_text(item, "Name") == "retrieveconsents"
    assert first_text(item, "MimeType") == "application/xml+process"
    # the process flow has subflow calls somewhere deep
    subflows = list(descendants_local(doc.tree, "subflow"))
    assert subflows
    assert lname(subflows[0]) == "subflow"


def test_malformed_xml_recovers_or_reports():
    from cai_docs.models import RawFile

    raw = RawFile(relpath="bad.xml", abs_path=None, ext="xml", data=b"<a><b></a>")
    doc = parse(raw)
    # recovering parser should still give us a tree (root 'a')
    assert doc.tree is not None
    assert doc.root_localname == "a"


def test_unparseable_is_flagged():
    from cai_docs.models import RawFile

    raw = RawFile(relpath="empty.xml", abs_path=None, ext="xml", data=b"")
    doc = parse(raw)
    assert doc.tree is None
    assert doc.parse_error


def test_json_sidecar():
    from cai_docs.models import RawFile

    raw = RawFile(
        relpath="meta.json", abs_path=None, ext="json", data=b'{"type":"process","name":"x"}'
    )
    doc = parse(raw)
    assert doc.json_sidecar == {"type": "process", "name": "x"}
