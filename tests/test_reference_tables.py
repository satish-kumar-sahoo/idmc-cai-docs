"""Reference-data XML (collection points, services, entry points, ...) is
rendered as readable tables; secret-looking cell values are redacted."""

from pathlib import Path

from cai_docs.classify import classify
from cai_docs.config import Config
from cai_docs.extract import extract
from cai_docs.models import RawFile
from cai_docs.pipeline import run
from cai_docs.xmlmodel import parse

SCM = Path(__file__).parent / "fixtures" / "scm"
REF = SCM / "Explore/Proj/config/collection_points.xml"


def _asset(p, relpath):
    rf = RawFile(relpath=relpath, abs_path=p, ext="xml", data=p.read_bytes())
    doc = parse(rf)
    at, conf, sig = classify(doc)
    return extract(doc, at, conf, sig)


def test_repeated_records_become_a_table():
    a = _asset(REF, "Explore/Proj/config/collection_points.xml")
    assert a.asset_type == "resource"
    assert len(a.tables) == 1
    t = a.tables[0]
    assert t.title == "collectionPoint"
    # union of leaf-child names across records (apiToken only on the 2nd)
    assert t.columns[:4] == ["nmsc", "country", "brand", "isValidationRequired"]
    assert "apiToken" in t.columns
    assert len(t.rows) == 2
    assert t.rows[0][:3] == ["TROM", "BG", "Toyota"]
    # missing column on a record yields an empty cell, not a shift
    apidx = t.columns.index("apiToken")
    assert t.rows[0][apidx] == ""
    # secret-looking value is redacted in the table
    assert t.rows[1][apidx] == "<redacted>"
    flat = "".join("".join(r) for r in t.rows)
    assert "eyJhbGciOiJIUzI1NiJ9" not in flat


def test_attribute_and_nested_list_shapes(tmp_path):
    # entry-point style: attributes as columns, nested same-tag list joined
    xml = """<?xml version="1.0"?>
<entry:entryPoints xmlns:entry="urn:x">
  <entry:entryPoint modelLocation="project:/a.bpel" serviceName="A">
    <entry:supportedContentTypes>
      <entry:contentType>text/xml</entry:contentType>
      <entry:contentType>application/xml</entry:contentType>
    </entry:supportedContentTypes>
  </entry:entryPoint>
  <entry:entryPoint modelLocation="project:/b.bpel" serviceName="B">
    <entry:supportedContentTypes>
      <entry:contentType>text/plain</entry:contentType>
    </entry:supportedContentTypes>
  </entry:entryPoint>
</entry:entryPoints>"""
    p = tmp_path / "Explore/x/config/entry-points.xml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(xml, encoding="utf-8")
    a = _asset(p, "Explore/x/config/entry-points.xml")
    t = a.tables[0]
    assert t.title == "entryPoint"
    assert "modelLocation" in t.columns and "serviceName" in t.columns
    assert "supportedContentTypes" in t.columns
    sct = t.rows[0][t.columns.index("supportedContentTypes")]
    assert sct == "text/xml; application/xml"


def test_reference_tables_render_in_vault(tmp_path):
    cfg = Config(input_path=SCM, output_dir=tmp_path / "v",
                 cache_dir=tmp_path / "c", use_llm=False)
    run(cfg)
    md = next(p for p in (tmp_path / "v").rglob("collection_points*.md"))
    txt = md.read_text(encoding="utf-8")
    assert "## Reference data" in txt
    assert "### collectionPoint (2 rows)" in txt
    assert "| nmsc | country | brand |" in txt
    assert "| TROM | BG | Toyota |" in txt
    assert "eyJhbGciOiJIUzI1NiJ9" not in txt  # no secret leaked into the table
