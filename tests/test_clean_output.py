"""A regen wipes the stale vault (so assets from another repo never linger)
but preserves the user's .obsidian settings, and refuses dangerous targets."""

from pathlib import Path

import pytest

from cai_docs.config import Config
from cai_docs.graph import build_graph
from cai_docs.models import Asset, RunReport
from cai_docs.render import VaultWriter


def test_clean_removes_stale_keeps_obsidian(tmp_path):
    out = tmp_path / "v"
    out.mkdir()
    (out / "OldAssetFromMasterRepo.md").write_text("stale", encoding="utf-8")
    (out / "spi.old").mkdir()
    (out / "spi.old" / "Gone.md").write_text("stale", encoding="utf-8")
    obs = out / ".obsidian"
    obs.mkdir()
    (obs / "workspace.json").write_text("{}", encoding="utf-8")

    a = Asset(source_relpath="New.PROCESS.xml", asset_type="process",
              name="New", guid="N1", confidence=0.9)
    a.static_summary = "New is a process."
    cfg = Config(input_path=Path("."), output_dir=out,
                 cache_dir=tmp_path / "c", use_llm=False)
    VaultWriter(cfg).write(build_graph([a]), RunReport())

    assert not (out / "OldAssetFromMasterRepo.md").exists()
    assert not (out / "spi.old").exists()
    assert (obs / "workspace.json").read_text(encoding="utf-8") == "{}"  # preserved
    stems = {p.stem for p in out.rglob("*.md")}
    assert "New" in stems


def test_clean_refuses_dangerous_targets(tmp_path, monkeypatch):
    root = Path(tmp_path.anchor or "/")
    with pytest.raises(ValueError, match="filesystem root"):
        VaultWriter._clean_output(root)
    # cwd (and its ancestors) must not be wiped
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match="cwd or an ancestor"):
        VaultWriter._clean_output(tmp_path)


def test_clean_noop_when_missing(tmp_path):
    VaultWriter._clean_output(tmp_path / "does-not-exist")  # must not raise
