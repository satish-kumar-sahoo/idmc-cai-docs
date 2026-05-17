"""IDMC source-control JSON sidecar handling.

The Git source-control layout stores each asset as ``Name.TYPE.xml`` plus a
hidden ``.Name.TYPE.json`` metadata sidecar carrying the authoritative
``objectInfo`` (stable id/GUID, type, description). Sidecars are metadata, not
separate assets: we pair them to their sibling and merge the metadata in. An
unpaired sidecar is still emitted (typed from its own ``objectInfo``) so
nothing is ever dropped.
"""

from __future__ import annotations

import json

from .models import Asset, RawFile

# objectInfo.type token -> canonical asset type
SIDECAR_TYPE_MAP = {
    "PROCESS": "process",
    "AI_CONNECTION": "connection",
    "CONNECTION": "connection",
    "AI_SERVICE_CONNECTOR": "service_connector",
    "BUSINESS_SERVICE": "service_connector",
    "PROCESS_OBJECT": "process_object",
    "GUIDE": "guide",
    "SCHEMA": "schema",
    "HIERARCHICAL_SCHEMA": "schema",
    "DEPLOYMENT": "deployment",
    "PROJECT": "project",
    "FOLDER": "project",
}

_KEEP_EXTS = ("xml", "bpel", "pdd", "wsdl", "xsd")


def _is_sidecar(rf: RawFile) -> bool:
    base = rf.relpath.rsplit("/", 1)[-1]
    return rf.ext == "json" and base.startswith(".")


def normalize_object_info(raw_json: str | bytes) -> dict | None:
    """Return {type,id,name,description,_cai_type} from a sidecar's objectInfo."""
    try:
        data = json.loads(raw_json)
    except (ValueError, TypeError):
        return None
    info = (data or {}).get("objectInfo")
    if not isinstance(info, dict):
        return None
    raw_type = str(info.get("type", "")).strip()
    add = info.get("metadata", {}).get("additionalInfo", {}) if isinstance(
        info.get("metadata"), dict
    ) else {}
    return {
        "type": raw_type,
        "_cai_type": SIDECAR_TYPE_MAP.get(raw_type.upper()),
        "id": info.get("id") or None,
        "name": info.get("name") or None,
        "description": (add.get("description") if isinstance(add, dict) else None) or "",
    }


def pair_sidecars(
    files: list[RawFile],
) -> tuple[list[RawFile], dict[str, dict]]:
    """Split files into (assets, {primary_relpath: normalized_objectInfo}).

    Paired sidecars are removed from the asset list; unpaired ones are kept.
    """
    by_dir: dict[str, dict[str, RawFile]] = {}
    for rf in files:
        d, _, name = rf.relpath.rpartition("/")
        by_dir.setdefault(d, {})[name] = rf

    sidecar_meta: dict[str, dict] = {}
    paired: set[str] = set()

    for rf in files:
        if not _is_sidecar(rf):
            continue
        d, _, name = rf.relpath.rpartition("/")
        base = name[1:]  # strip leading dot
        if base.lower().endswith(".json"):
            base = base[:-5]
        # primary candidate: same dir, base + a known asset extension
        siblings = by_dir.get(d, {})
        primary = None
        for ext in _KEEP_EXTS:
            cand = f"{base}.{ext}"
            if cand in siblings:
                primary = siblings[cand]
                break
        if primary is None and base in siblings:
            primary = siblings[base]
        if primary is None:
            continue  # unpaired -> stays an asset (still documented)
        info = normalize_object_info(rf.data)
        if info:
            sidecar_meta[primary.relpath] = info
            paired.add(rf.relpath)

    assets = [f for f in files if f.relpath not in paired]
    return assets, sidecar_meta


def apply_sidecar(asset: Asset, info: dict, threshold: float = 0.45) -> None:
    """Merge authoritative sidecar metadata into an extracted asset."""
    if info.get("id"):
        asset.guid = info["id"]  # IDMC object id is the reference key
    if info.get("name") and not asset.name:
        asset.name = info["name"]
    if info.get("description") and not asset.description:
        asset.description = info["description"]
    cai_type = info.get("_cai_type")
    if cai_type and (asset.asset_type == "unknown" or asset.confidence < 0.75):
        asset.asset_type = cai_type
        asset.confidence = max(asset.confidence, 0.9)
        asset.classification_signals.append(
            f"sidecar objectInfo.type={info.get('type')!r} -> {cai_type} (+authoritative)"
        )
    asset.needs_review = (
        asset.asset_type == "unknown" or asset.confidence < threshold
    )
