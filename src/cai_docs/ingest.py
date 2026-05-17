"""Stage 1: turn a .zip export or a directory into a flat list of RawFile.

Handles zip-slip safely and recursively expands nested per-asset zips
(Informatica exports often nest a zip per asset).
"""

from __future__ import annotations

import tempfile
import zipfile
from pathlib import Path

from .models import RawFile

# Files we never surface as assets (binary noise / VCS metadata).
_SKIP_DIRS = {".git", "__MACOSX", ".cai-work"}
_SKIP_EXTS = {"zip"}  # nested zips are expanded, not emitted as RawFile


class ZipSlipError(Exception):
    """Raised when a zip entry would extract outside the destination root."""


def _is_within(root: Path, target: Path) -> bool:
    try:
        target.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _safe_extract(zip_path: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        for member in zf.namelist():
            # Normalise and reject absolute / parent-escaping members.
            out_path = dest / member
            if member.startswith(("/", "\\")) or ".." in Path(member).parts:
                raise ZipSlipError(f"unsafe zip entry: {member!r} in {zip_path.name}")
            if not _is_within(dest, out_path):
                raise ZipSlipError(f"zip-slip blocked: {member!r} in {zip_path.name}")
        zf.extractall(dest)


def _expand_nested_zips(root: Path) -> None:
    """Recursively expand any .zip found under root into a sibling folder."""
    seen: set[Path] = set()
    while True:
        nested = [
            p
            for p in root.rglob("*.zip")
            if p not in seen and p.is_file() and not any(d in p.parts for d in _SKIP_DIRS)
        ]
        if not nested:
            break
        for zp in nested:
            seen.add(zp)
            target = zp.with_suffix("")  # foo.zip -> foo/
            suffix = 1
            while target.exists():
                target = zp.parent / f"{zp.stem}__{suffix}"
                suffix += 1
            try:
                _safe_extract(zp, target)
            except (zipfile.BadZipFile, ZipSlipError):
                # leave the .zip in place; it just won't be expanded
                continue


def discover(input_path: str | Path) -> list[RawFile]:
    """Return every documentable file under the export, recursively.

    `input_path` may be a directory or a .zip file.
    """
    input_path = Path(input_path)
    if not input_path.exists():
        raise FileNotFoundError(input_path)

    if input_path.is_dir():
        root = input_path
    else:
        if not zipfile.is_zipfile(input_path):
            raise ValueError(f"{input_path} is neither a directory nor a zip archive")
        work = Path(tempfile.mkdtemp(prefix="cai-docs-"))
        _safe_extract(input_path, work)
        _expand_nested_zips(work)
        root = work

    files: list[RawFile] = []
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        parts = set(path.relative_to(root).parts)
        if parts & _SKIP_DIRS:
            continue
        ext = path.suffix.lstrip(".").lower()
        if ext in _SKIP_EXTS:
            continue
        try:
            data = path.read_bytes()
        except OSError:
            continue
        files.append(
            RawFile(
                relpath=path.relative_to(root).as_posix(),
                abs_path=path,
                ext=ext,
                data=data,
            )
        )
    return files
