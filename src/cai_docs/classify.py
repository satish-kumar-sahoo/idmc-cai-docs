"""Stage 3: classify an XmlDoc into an asset type.

Schema-profile-first with adaptive fallback. Strong, validated signals
(MimeType, the Entry payload root, filename / contribution-id suffixes) are
weighted heavily; weaker structural hints back them up. Anything that does not
score above the configured threshold is returned as ``unknown`` (still
documented downstream, just flagged).
"""

from __future__ import annotations

from .models import XmlDoc
from .xmlmodel import children_local, first_text, lname

PROCESS = "process"
SERVICE_CONNECTOR = "service_connector"
CONNECTION = "connection"
PROCESS_OBJECT = "process_object"
GUIDE = "guide"
SCHEMA = "schema"
UNKNOWN = "unknown"

ASSET_TYPES = (PROCESS, SERVICE_CONNECTOR, CONNECTION, PROCESS_OBJECT, GUIDE, SCHEMA)

_SATURATION = 6.0

# Local-name of the Entry payload root -> asset type (strongest structural signal).
_ENTRY_ROOT = {
    "process": PROCESS,
    "serviceconnector": SERVICE_CONNECTOR,
    "connection": CONNECTION,
    "processobject": PROCESS_OBJECT,
    "guide": GUIDE,
    "schema": SCHEMA,
}

# Order matters: check the more specific substrings before "process"/"connection".
_MIME_ORDER = [
    ("serviceconnector", SERVICE_CONNECTOR),
    ("processobject", PROCESS_OBJECT),
    ("connection", CONNECTION),
    ("guide", GUIDE),
    ("schema", SCHEMA),
    ("process", PROCESS),
]

_FILENAME_SUFFIX = {
    ".process.xml": PROCESS,
    ".serviceconnector.xml": SERVICE_CONNECTOR,
    ".connection.xml": CONNECTION,
    ".processobject.xml": PROCESS_OBJECT,
    ".guide.xml": GUIDE,
    ".xsd": SCHEMA,
}

_CONTRIB_SUFFIX = {
    ".pd.xml": PROCESS,
    ".sc.xml": SERVICE_CONNECTOR,
    ".conn.xml": CONNECTION,
    ".po.xml": PROCESS_OBJECT,
    ".gd.xml": GUIDE,
}


def _entry_payload_root(doc: XmlDoc):
    """The first element child of types1:Entry (the actual asset), if any."""
    if doc.tree is None:
        return None
    for item in children_local(doc.tree, "Item"):
        for entry in children_local(item, "Entry"):
            for child in entry:
                if isinstance(child.tag, str):
                    return child
    return None


def classify(doc: XmlDoc) -> tuple[str, float, list[str]]:
    scores: dict[str, float] = dict.fromkeys(ASSET_TYPES, 0.0)
    signals: list[str] = []

    def add(asset_type: str, weight: float, why: str) -> None:
        if asset_type in scores:
            scores[asset_type] += weight
            signals.append(f"{why} -> {asset_type} (+{weight:g})")

    # JSON sidecar authoritative 'type'
    if doc.json_sidecar and isinstance(doc.json_sidecar, dict):
        t = str(doc.json_sidecar.get("type", "")).lower()
        for key, at in _ENTRY_ROOT.items():
            if key in t:
                add(at, 4.0, f"json sidecar type={t!r}")
                break

    if doc.tree is not None:
        # Strong: Entry payload root element
        payload = _entry_payload_root(doc)
        if payload is not None:
            ln = lname(payload).lower()
            if ln in _ENTRY_ROOT:
                add(_ENTRY_ROOT[ln], 4.0, f"Entry payload root <{lname(payload)}>")

        # Strong: MimeType text
        mime = ""
        for item in children_local(doc.tree, "Item"):
            mime = first_text(item, "MimeType").lower()
            if mime:
                break
        for needle, at in _MIME_ORDER:
            if needle in mime:
                add(at, 3.0, f"MimeType {mime!r}")
                break

        # Bare (non-enveloped) export: root itself is the payload
        rln = (doc.root_localname or "").lower()
        if rln in _ENTRY_ROOT:
            add(_ENTRY_ROOT[rln], 2.0, f"root element <{doc.root_localname}>")
        if rln == "schema" and any(
            "XMLSchema" in uri for uri in doc.namespaces.values()
        ):
            add(SCHEMA, 3.0, "XSD root <schema> in XMLSchema namespace")

        # Medium: PublishedContributionId suffix
        for item in children_local(doc.tree, "Item"):
            contrib = first_text(item, "PublishedContributionId").lower()
            for suffix, at in _CONTRIB_SUFFIX.items():
                if contrib.endswith(suffix):
                    add(at, 2.0, f"contributionId ~ {suffix}")
            entry_id = first_text(item, "EntryId").lower()
            if entry_id.endswith("::pd.xml"):
                add(PROCESS, 1.0, "EntryId ~ ::pd.xml")
            break

    # Medium: filename suffix
    name = doc.relpath.lower()
    for suffix, at in _FILENAME_SUFFIX.items():
        if name.endswith(suffix):
            add(at, 2.0, f"filename ~ {suffix}")
            break

    best = max(scores, key=lambda k: scores[k])
    top = scores[best]
    if top <= 0.0:
        return UNKNOWN, 0.0, signals or ["no matching signals"]
    confidence = min(1.0, top / _SATURATION)
    return best, round(confidence, 3), signals
