"""Stage 5: resolve cross-asset references into a dependency graph.

Indexes assets by GUID and by (normalised) name, resolves each Reference to a
target asset where possible, and records reverse "used by" edges. Unresolved
references become external nodes so they are still surfaced on pages.
"""

from __future__ import annotations

from .models import Asset, AssetGraph, Edge

_EDGE_KIND = {
    "subprocess": "calls-subprocess",
    "connection": "uses-connection",
    "service_connector": "uses-service-connector",
    "connector_hint": "uses-connection",
    "catalog_resource": "references-resource",
}


def _norm(s: str | None) -> str:
    if not s:
        return ""
    return "".join(ch for ch in s.lower() if ch.isalnum())


_CONN_PREFIXES = (
    "serviceconnector-", "serviceconnector_", "appconnection-",
    "appconnection_", "connection-",
)
_REF_EXTS = (".xml", ".wsdl", ".xsd", ".bpel", ".pdd", ".json", ".pd")


def _strip_connector_prefix(name: str) -> str:
    # "ServiceConnector-OT-Submit-Consent" / "AppConnection-Foo" -> "OT-Submit-Consent"/"Foo"
    low = (name or "").lower()
    for pre in _CONN_PREFIXES:
        if low.startswith(pre):
            return name[len(pre):]
    return name or ""


def _basename_key(value: str | None) -> str:
    """'project:/SaaSGlobal/metadata/purposes.xml' / '../wsdl/x.wsdl' -> base name."""
    if not value:
        return ""
    v = value.split("?", 1)[0].split("#", 1)[0]
    v = v.split("project:/", 1)[-1].split("contribution:/", 1)[-1]
    v = v.replace("\\", "/").rstrip("/")
    base = v.rsplit("/", 1)[-1]
    low = base.lower()
    for ext in _REF_EXTS:
        if low.endswith(ext):
            base = base[: -len(ext)]
            low = base.lower()
    # also drop a trailing IDMC type infix (foo.AI_CONNECTION -> foo)
    for infix in (".ai_service_connector", ".ai_connection", ".process_object",
                  ".process", ".serviceconnector", ".connection", ".guide"):
        if low.endswith(infix):
            base = base[: -len(infix)]
            break
    return base


# Which asset types a reference of a given kind should prefer to bind to.
_EXPECT = {
    "subprocess": ("process",),
    "connection": ("connection", "service_connector", "process"),
    "service_connector": ("service_connector", "connection"),
    "connector_hint": ("connection", "service_connector"),
    "catalog_resource": (
        "resource", "schema", "process_object", "process",
        "guide", "connection", "service_connector",
    ),
}


def build_graph(assets: list[Asset]) -> AssetGraph:
    g = AssetGraph(assets=list(assets))

    by_guid: dict[str, Asset] = {}
    by_name: dict[str, list[Asset]] = {}
    for a in assets:
        if a.guid:
            by_guid.setdefault(a.guid, a)
        keys = {
            a.name,
            a.display_name,
            _basename_key(a.source_relpath),
            _strip_connector_prefix(a.name),
        }
        for nm in keys:
            if nm:
                bucket = by_name.setdefault(_norm(nm), [])
                if a not in bucket:
                    bucket.append(a)

    def resolve(ref) -> Asset | None:
        if ref.target_guid and ref.target_guid in by_guid:
            return by_guid[ref.target_guid]
        cands: list[Asset] = []
        seen_keys: set[str] = set()
        for cand in (
            ref.target_name,
            _strip_connector_prefix(ref.target_name or ""),
            _basename_key(ref.raw),
            _basename_key(ref.target_name),
            _strip_connector_prefix(_basename_key(ref.raw)),
        ):
            for a in by_name.get(_norm(cand or ""), []):
                if a.key not in seen_keys:
                    seen_keys.add(a.key)
                    cands.append(a)
        if not cands:
            return None
        cands.sort(key=lambda x: x.key or "")  # deterministic tie-break
        for want in _EXPECT.get(ref.kind, ()):  # type-aware preference
            for c in cands:
                if c.asset_type == want:
                    return c
        return cands[0]

    seen: set[tuple[str, str, str]] = set()

    def link(src: Asset, target_key: str, kind: str, target_name: str | None, resolved: bool):
        sig = (src.key, target_key, kind)
        if sig in seen:
            return
        seen.add(sig)
        edge = Edge(
            source_key=src.key,
            target_key=target_key,
            kind=kind,
            target_name=target_name,
            resolved=resolved,
        )
        g.edges.append(edge)
        g.uses.setdefault(src.key, []).append(edge)
        if resolved:
            g.used_by.setdefault(target_key, []).append(edge)

    for a in assets:
        for ref in a.references:
            kind = _EDGE_KIND.get(ref.kind, "references-resource")
            target = resolve(ref)
            if target is not None and target.key != a.key:
                link(a, target.key, kind, target.name, resolved=True)
            elif target is None:
                label = ref.target_name or ref.raw or "external"
                link(a, f"external:{kind}:{label}", kind, label, resolved=False)
                g.unresolved.append(ref)

    return g
