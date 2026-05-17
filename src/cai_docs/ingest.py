"""Stage 1: turn a .zip export or a directory into a flat list of RawFile.

Handles zip-slip safely and recursively expands nested per-asset zips
(Informatica exports often nest a zip per asset).
"""

from __future__ import annotations

import tempfile
import zipfile
from pathlib import Path

from .models import RawFile

# Directories that never contain documentable CAI assets.
_SKIP_DIRS = {
    ".git", "__MACOSX", ".cai-work", ".github", ".settings", "scripts",
    ".image",  # IDMC stores process-rendering/layout XML here (presentation only)
}

# Extensions that are never CAI assets: nested zips (expanded separately),
# images, and project/build/CI scaffolding files.
_SKIP_EXTS = {
    "zip",
    # images
    "jpg", "jpeg", "png", "gif", "svg", "ico", "bmp",
    # project / build / CI scaffolding
    "project", "buildpath", "bpr", "yml", "yaml", "properties",
    "md", "py", "txt", "classpath", "gitignore", "gitattributes",
    "log", "lock", "sh", "bat",
}

# Asset-relevant extensions kept even if they look unusual.
_KEEP_EXTS = {"xml", "json", "bpel", "pdd", "wsdl", "xsd", "xslt", "xsl"}

# Directory/project scaffolding the repo writes per folder (OData listing +
# folder/project marker). Not CAI assets. Leading dot is optional.
_SKIP_NAME_SUFFIXES = (".folder.json", ".project.json")


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
        # Allowlist: only emit asset-relevant files. This drops images,
        # .gitignore/.project/CI scaffolding, etc. without per-type rules.
        if ext not in _KEEP_EXTS or ext in _SKIP_EXTS:
            continue
        lname = path.name.lower()
        if any(lname.endswith(s) for s in _SKIP_NAME_SUFFIXES):
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
