"""Expressions render as a summary table plus a properly formatted code
block per expression (full XQuery, not a truncated one-liner)."""

from pathlib import Path

from cai_docs.config import Config
from cai_docs.graph import build_graph
from cai_docs.models import Asset, ExpressionItem, RunReport
from cai_docs.render import VaultWriter
from cai_docs.render import _expr_rows, _fence, _format_code

_XQ = (
    "if( fn:contains($output.Code,'504') )\n"
    "    then 'Gateway Timeout'\n"
    "    else 'Unexpected exception'"
)


def test_format_code_dedents_without_mangling():
    raw = "\n\n    let $x := 1\n        return $x\n  \n"
    out = _format_code(raw)
    assert out == "let $x := 1\n    return $x"  # common indent removed, edges trimmed
    # newlines preserved (not collapsed to spaces)
    assert "\n" in out


def test_fence_escapes_when_body_has_backticks():
    assert _fence("no ticks") == "```"
    assert _fence("a ``` b") == "````"


def test_expr_rows_shape():
    rows = _expr_rows([
        ExpressionItem(expression=_XQ, language="XQuery",
                        target="temp.msg", context="operation"),
        ExpressionItem(expression="select 1", language="SQL", target=None),
    ])
    assert rows[0]["n"] == 1 and rows[0]["target"] == "temp.msg"
    assert rows[0]["lines"] == 3 and rows[0]["lang_tag"] == "xquery"
    assert rows[1]["lang_tag"] == "sql" and rows[1]["lines"] == 1


def test_expression_section_rendered_as_table_and_code(tmp_path):
    a = Asset(source_relpath="P.PROCESS.xml", asset_type="process",
              name="P", guid="P1", confidence=0.95)
    a.expressions = [
        ExpressionItem(expression=_XQ, language="XQuery",
                       target="temp.errMsg", context="operation"),
    ]
    a.static_summary = "P is a process."
    g = build_graph([a])
    cfg = Config(input_path=Path("."), output_dir=tmp_path / "v",
                 cache_dir=tmp_path / "c", use_llm=False)
    VaultWriter(cfg).write(g, RunReport())
    md = next(p for p in (tmp_path / "v").rglob("P*.md")
              if not p.stem.startswith(("_MOC", "Home"))).read_text(encoding="utf-8")

    assert "## Expressions" in md
    assert "| # | Target | Language | Context | Lines |" in md
    assert "| 1 | `temp.errMsg`" not in md  # target shown unbackticked in table
    assert "| 1 | temp.errMsg | XQuery | operation | 3 |" in md
    assert "```xquery" in md
    # full multi-line expression preserved verbatim, not truncated to one line
    assert "    then 'Gateway Timeout'" in md
    assert "…" not in md.split("## Expressions")[1].split("## ")[0]
