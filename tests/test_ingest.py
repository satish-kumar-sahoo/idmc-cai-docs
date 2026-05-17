import io
import zipfile
from pathlib import Path

import pytest

from cai_docs.ingest import ZipSlipError, discover


def _make_zip(path: Path, entries: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)


def test_discover_directory(tmp_path: Path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "proc.xml").write_text("<process/>", encoding="utf-8")
    (tmp_path / "note.txt").write_text("hi", encoding="utf-8")

    files = discover(tmp_path)
    rels = {f.relpath for f in files}
    assert rels == {"a/proc.xml", "note.txt"}
    proc = next(f for f in files if f.relpath == "a/proc.xml")
    assert proc.ext == "xml"
    assert proc.data == b"<process/>"


def test_discover_zip(tmp_path: Path):
    zp = tmp_path / "export.zip"
    _make_zip(zp, {"x/proc.PROCESS.xml": b"<process/>", "readme.md": b"# hi"})

    files = discover(zp)
    rels = {f.relpath for f in files}
    assert rels == {"x/proc.PROCESS.xml", "readme.md"}


def test_nested_zip_is_expanded(tmp_path: Path):
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w") as zf:
        zf.writestr("inner_proc.xml", b"<process/>")
    zp = tmp_path / "outer.zip"
    _make_zip(zp, {"asset.zip": inner.getvalue(), "top.xml": b"<a/>"})

    files = discover(zp)
    rels = {f.relpath for f in files}
    # nested zip itself is not emitted, its contents are
    assert not any(r.endswith(".zip") for r in rels)
    assert "top.xml" in rels
    assert any(r.endswith("inner_proc.xml") for r in rels)


def test_zip_slip_blocked(tmp_path: Path):
    zp = tmp_path / "evil.zip"
    _make_zip(zp, {"../escape.txt": b"pwned", "ok.xml": b"<a/>"})
    with pytest.raises(ZipSlipError):
        discover(zp)
