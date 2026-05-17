"""Regression: note-name collisions + type-aware resolution (the graph bug).

Root cause was: a .bpel process and its .pdd deployment share a basename, so
[[Name]] wikilinks were ambiguous in Obsidian and subprocess refs bound to the
deployment instead of the process, orphaning the real process node.
"""

from pathlib import Path

from cai_docs.config import Config
from cai_docs.graph import build_graph
from cai_docs.models import Asset, Reference
from cai_docs.pipeline import run
from cai_docs.render import VaultWriter


def _cfg(tmp_path):
    return Config(input_path=Path("."), output_dir=tmp_path / "v",
                  cache_dir=tmp_path / "c", use_llm=False)


def test_note_names_globally_unique_and_type_qualified(tmp_path):
    a = Asset(source_relpath="bpel/Save.bpel", asset_type="process",
              name="Save", guid="P1")
    b = Asset(source_relpath="deploy/Save.pdd", asset_type="deployment",
              name="Save", guid="D1")
    c = Asset(source_relpath="schema/Save.xsd", asset_type="schema",
              name="Save", guid="S1")
    names = VaultWriter(_cfg(tmp_path))._assign_note_names([a, b, c])
    vals = list(names.values())
    assert len(set(v.lower() for v in vals)) == 3, vals  # all unique
    assert names[a.key] == "Save"  # process keeps the bare name (priority)
    assert names[b.key] == "Save (deployment)"
    assert names[c.key] == "Save (schema)"
    # deterministic across calls
    assert VaultWriter(_cfg(tmp_path))._assign_note_names([c, b, a]) == names


def test_subprocess_ref_binds_to_process_not_deployment():
    caller = Asset(source_relpath="P.PROCESS.xml", asset_type="process",
                   name="Caller", guid="C1")
    caller.references = [Reference(kind="subprocess", raw="Save",
                                   target_name="Save")]
    proc = Asset(source_relpath="bpel/Save.bpel", asset_type="process",
                 name="Save", guid="P1")
    dep = Asset(source_relpath="deploy/Save.pdd", asset_type="deployment",
                name="Save", guid="D1")
    g = build_graph([caller, dep, proc])  # dep listed before proc on purpose
    edges = g.uses[caller.key]
    assert len(edges) == 1
    assert edges[0].resolved
    assert edges[0].target_key == proc.key  # the process, not the deployment


def test_connection_ref_prefers_connection_over_process():
    caller = Asset(source_relpath="P.PROCESS.xml", asset_type="process",
                   name="Caller", guid="C1")
    caller.references = [Reference(kind="connection", raw="OT-Login",
                                   target_name="OT-Login")]
    proc = Asset(source_relpath="OT-Login.PROCESS.xml", asset_type="process",
                 name="OT-Login", guid="P9")
    conn = Asset(source_relpath="AppConnection-OT-Login.AI_CONNECTION.xml",
                 asset_type="connection", name="AppConnection-OT-Login",
                 guid="K1")
    g = build_graph([caller, proc, conn])
    tgt = g.uses[caller.key][0]
    assert tgt.resolved and tgt.target_key == conn.key


def test_scm_pipeline_no_duplicate_basenames_and_pdd_merged(tmp_path):
    scm = Path(__file__).parent / "fixtures" / "scm"
    cfg = Config(input_path=scm, output_dir=tmp_path / "v",
                 cache_dir=tmp_path / "c", use_llm=False)
    rep = run(cfg)
    vault = tmp_path / "v"
    asset_md = [
        p for p in vault.rglob("*.md")
        if not (p.stem.startswith("_MOC") or p.stem == "Home")
        and "/_External/" not in p.as_posix()
    ]
    stems = [p.stem for p in asset_md]
    assert len(stems) == len(set(stems)), "duplicate basenames remain"
    assert not any("(2)" in s for s in stems), stems
    assert rep.counts_by_type.get("deployment", 0) == 0  # .pdd merged
    # the bpel process exists exactly once and carries deployment info
    saves = [p for p in asset_md if p.stem == "SaveConfig"]
    assert len(saves) == 1
    txt = saves[0].read_text(encoding="utf-8")
    assert "deployment descriptor merged" in txt
    assert "deploy.location" in txt
