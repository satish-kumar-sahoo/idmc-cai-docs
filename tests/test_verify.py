"""Post-render Mermaid verification: catches the exact breakages this engine
has hit (unquoted edge labels) plus structural problems, and runs as part of
every pipeline run."""

from pathlib import Path

from cai_docs.config import Config
from cai_docs.pipeline import run
from cai_docs.verify import verify_file, verify_mermaid, verify_vault

SCM = Path(__file__).parent / "fixtures" / "scm"

_GOOD = """flowchart TD
    n0(["start"])
    n1["Assignment to x"]
    n2{"decision"}
    n3[["SubProc"]]
    n4(["End"])
    n0 --> n1
    n1 -->|"string-equals($t.x, 'A')"| n2
    n2 -.->|"fault: err"| n3
    n3 --> n4
classDef svc fill:#dcfce7;
class n3 svc;"""


def test_valid_diagram_has_no_issues():
    assert verify_mermaid(_GOOD) == []


def test_unquoted_edge_label_is_flagged():
    bad = "flowchart TD\n    n0[\"a\"]\n    n1[\"b\"]\n    n0 -->|string-equals($t.x, 1)| n1"
    issues = verify_mermaid(bad)
    assert any("unquoted edge label" in i for i in issues), issues


def test_quoted_edge_label_passes():
    ok = 'flowchart TD\n    n0["a"]\n    n1["b"]\n    n0 -->|"string-equals($t.x, 1)"| n1'
    assert verify_mermaid(ok) == []


def test_edge_to_undefined_node_flagged():
    bad = 'flowchart TD\n    n0["a"]\n    n0 --> n9'
    assert any("undefined node 'n9'" in i for i in verify_mermaid(bad))


def test_empty_label_and_unbalanced_quotes_and_header():
    assert any("empty node label" in i for i in verify_mermaid('flowchart TD\n    n0[""]'))
    assert any(
        "unbalanced quotes" in i
        for i in verify_mermaid('flowchart TD\n    n0["a]\n    n1["b"]')
    )
    assert any("bad/missing header" in i for i in verify_mermaid("digraph {\n a -> b\n}"))


def test_verify_file_detects_unclosed_fence(tmp_path):
    p = tmp_path / "x.md"
    p.write_text("# x\n```mermaid\nflowchart TD\n    n0[\"a\"]\n", encoding="utf-8")
    assert any("unclosed" in m for m in verify_file(p))


def test_pipeline_runs_verification_and_scm_vault_is_clean(tmp_path):
    cfg = Config(input_path=SCM, output_dir=tmp_path / "v",
                 cache_dir=tmp_path / "c", use_llm=False)
    rep = run(cfg)
    assert rep.mermaid_blocks >= 1
    assert rep.mermaid_issues == [], rep.mermaid_issues
    # report surfaces the verification result
    assert "mermaid diagrams" in rep.render()
    blocks, issues = verify_vault(tmp_path / "v")
    assert blocks == rep.mermaid_blocks and issues == []
