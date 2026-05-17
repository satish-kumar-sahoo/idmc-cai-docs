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


def _strip_connector_prefix(name: str) -> str:
    # "ServiceConnector-OT-Submit-Consent" / "AppConnection-Foo" -> "OT-Submit-Consent"/"Foo"
    low = name.lower()
    for pre in ("serviceconnector-", "serviceconnector_", "appconnection-", "connection-"):
        if low.startswith(pre):
            return name[len(pre):]
    return name


def build_graph(assets: list[Asset]) -> AssetGraph:
    g = AssetGraph(assets=list(assets))

    by_guid: dict[str, Asset] = {}
    by_name: dict[str, Asset] = {}
    for a in assets:
        if a.guid:
            by_guid.setdefault(a.guid, a)
        for nm in (a.name, a.display_name, a.source_relpath.rsplit("/", 1)[-1]):
            if nm:
                by_name.setdefault(_norm(nm), a)

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
            target: Asset | None = None
            if ref.target_guid and ref.target_guid in by_guid:
                target = by_guid[ref.target_guid]
            if target is None:
                for cand in (ref.target_name, _strip_connector_prefix(ref.target_name or "")):
                    if cand and _norm(cand) in by_name:
                        target = by_name[_norm(cand)]
                        break
            if target is not None and target.key != a.key:
                link(a, target.key, kind, target.name, resolved=True)
            elif target is None:
                label = ref.target_name or ref.raw or "external"
                link(a, f"external:{kind}:{label}", kind, label, resolved=False)
                g.unresolved.append(ref)

    return g
