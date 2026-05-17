"""Stage 7: write the Obsidian vault.

Per-asset pages (frontmatter, Mermaid flow, interface, SQL, links), per-type
MOC index pages, and a Home page. Note names are made unique so Obsidian
`[[wikilink]]` resolution never dangles.
"""

from __future__ import annotations

import re
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from ..config import Config
from ..graph import _norm
from ..models import Asset, AssetGraph, RunReport

_TEMPLATES = Path(__file__).parent / "templates"
_SAFE = re.compile(r"[^A-Za-z0-9 ._-]+")
_TYPE_TITLE = {
    "process": "Processes",
    "service_connector": "Service Connectors",
    "connection": "Connections",
    "process_object": "Process Objects",
    "guide": "Guides",
    "schema": "Schemas",
    "deployment": "Deployments",
    "resource": "Resources",
    "project": "Projects",
    "unknown": "Unclassified",
}


def _sanitize(name: str) -> str:
    name = _SAFE.sub("-", (name or "").strip()).strip("-. ")
    return name or "unnamed"


def _mermaid_label(text: str, limit: int = 48) -> str:
    text = re.sub(r"\s+", " ", (text or "").strip())
    text = text.replace('"', "'").translate({ord(c): None for c in "[]{}|()<>"})
    if len(text) > limit:
        text = text[: limit - 1] + "…"
    return text or "step"


def _edge_label(text: str, limit: int = 40) -> str:
    """Edge labels tolerate ()/, so keep them readable; only kill pipes/quotes."""
    text = re.sub(r"\s+", " ", (text or "").strip())
    text = text.replace('"', "'").translate({ord(c): None for c in "|[]{}<>"})
    if len(text) > limit:
        text = text[: limit - 1] + "…"
    return text or "cond"


class VaultWriter:
    def __init__(self, config: Config):
        self.config = config
        self.env = Environment(
            loader=FileSystemLoader(str(_TEMPLATES)),
            autoescape=select_autoescape(enabled_extensions=()),
            trim_blocks=True,
            lstrip_blocks=True,
            keep_trailing_newline=True,
        )

    # --- naming -------------------------------------------------------------

    # Higher-priority types keep the bare name; others get a type-qualified one.
    _TYPE_PRIORITY = {
        "process": 0, "service_connector": 1, "connection": 2,
        "process_object": 3, "guide": 4, "schema": 5, "resource": 6,
        "deployment": 7, "project": 8, "unknown": 9,
    }

    def _assign_note_names(self, assets: list[Asset]) -> dict[str, str]:
        """Globally-unique, deterministic note names.

        Obsidian resolves [[Name]] by basename, so every note MUST have a
        unique basename or links land on the wrong asset / orphan duplicates.
        On collision we disambiguate by asset type (then an index), and order
        deterministically so the same input always yields the same names.
        """
        names: dict[str, str] = {}
        used: set[str] = set()
        ordered = sorted(
            assets,
            key=lambda a: (self._TYPE_PRIORITY.get(a.asset_type, 9), a.key or ""),
        )
        for a in ordered:
            base = _sanitize(a.display_name or a.name or a.key)
            candidate = base
            if candidate.lower() in used:
                kind = a.asset_type.replace("_", " ")
                candidate = f"{base} ({kind})"
            n = 2
            while candidate.lower() in used:
                kind = a.asset_type.replace("_", " ")
                candidate = f"{base} ({kind} {n})"
                n += 1
            used.add(candidate.lower())
            names[a.key] = candidate
        return names

    # --- frontmatter --------------------------------------------------------

    def _frontmatter(self, a: Asset) -> str:
        tags = [f"cai/{a.asset_type}"]
        if a.needs_review:
            tags.append("needs-review")
        fields = {
            "type": a.asset_type,
            "guid": a.guid or "",
            "name": a.name,
            "project": a.project_path or "",
            "version": a.version_label or "",
            "publication_status": a.publication_status or "",
            "modified_by": a.modified_by or "",
            "modified_date": a.modification_date or "",
            "source_path": a.source_relpath,
            "confidence": a.confidence,
        }
        lines = ["---"]
        for k, v in fields.items():
            v = str(v).replace('"', "'")
            lines.append(f'{k}: "{v}"' if v != "" else f"{k}: ")
        lines.append("tags: [" + ", ".join(tags) + "]")
        lines.append("---")
        return "\n".join(lines)

    # --- mermaid ------------------------------------------------------------

    def _resolve_ref_note(
        self, kind: str, guid: str | None, name: str | None,
        by_guid: dict[str, str], by_name: dict[str, str],
    ) -> str | None:
        if guid and guid in by_guid:
            return by_guid[guid]
        if name and _norm(name) in by_name:
            return by_name[_norm(name)]
        return None

    def _mermaid_flow(self, a: Asset, by_guid, by_name) -> str | None:
        if not a.flow or not a.flow.nodes:
            return None
        ids = {n.id for n in a.flow.nodes}
        mid: dict[str, str] = {nid: f"n{i}" for i, nid in enumerate(sorted(ids))}
        lines = ["flowchart TD"]
        clicks: list[str] = []
        cls: dict[str, list[str]] = {
            "subproc": [], "svc": [], "decision": [], "err": []
        }
        for node in a.flow.nodes:
            mi = mid[node.id]
            label = _mermaid_label(node.title or node.kind)
            if node.kind == "subflow":
                cls["subproc"].append(mi)
            elif node.kind == "service":
                cls["svc"].append(mi)
            elif node.kind == "container":
                cls["decision"].append(mi)
            elif node.kind == "throw":
                cls["err"].append(mi)
            if node.kind == "start":
                shape = f'{mi}(["start"])'
            elif node.kind == "end":
                shape = f'{mi}(["{label}"])'
            elif node.kind == "container":
                ctype = node.attrs.get("type", "exclusive")
                if ctype == "exclusive":
                    shape = f'{mi}{{"{label}"}}'
                else:
                    shape = f'{mi}[/"{label} (parallel)"/]'
            elif node.kind == "subflow":
                tgt = self._resolve_ref_note(
                    "subprocess", node.details.get("subflowGUID"),
                    node.details.get("subflowPath"), by_guid, by_name,
                )
                lbl = node.details.get("subflowPath") or label
                shape = f'{mi}[["{_mermaid_label(lbl)}"]]'
                if tgt:
                    clicks.append(f'click {mi} "{tgt}"')
            elif node.kind == "service":
                sname = node.details.get("serviceName", "")
                conn = sname.split(":", 1)[0].split("-", 1)[-1] if sname else ""
                tgt = self._resolve_ref_note("connection", None, conn, by_guid, by_name)
                shape = f'{mi}["{_mermaid_label(node.title or sname)}"]'
                if tgt:
                    clicks.append(f'click {mi} "{tgt}"')
            elif node.kind == "throw":
                shape = f'{mi}["⚠ {label}"]'
            else:
                shape = f'{mi}["{label}"]'
            lines.append(f"    {shape}")
        # dedupe; drop a plain reverse edge when the forward one exists
        emitted: set[tuple[str, str]] = set()
        pairs = {(e.source, e.target) for e in a.flow.edges}
        for e in a.flow.edges:
            if e.source not in mid or e.target not in mid:
                continue
            key = (e.source, e.target)
            if key in emitted:
                continue
            if not e.condition and (e.target, e.source) in pairs and key[0] > key[1]:
                continue  # keep one direction of a plain A<->B pair
            emitted.add(key)
            s, t = mid[e.source], mid[e.target]
            if e.condition:
                lines.append(f"    {s} -->|{_edge_label(e.condition)}| {t}")
            elif e.kind == "catch":
                lines.append(f"    {s} -.->|error| {t}")
            else:
                lines.append(f"    {s} --> {t}")
        lines.extend(f"    {c}" for c in clicks)
        lines += [
            "classDef subproc fill:#dbeafe,stroke:#2563eb;",
            "classDef svc fill:#dcfce7,stroke:#16a34a;",
            "classDef decision fill:#fef9c3,stroke:#ca8a04;",
            "classDef err fill:#fee2e2,stroke:#dc2626;",
        ]
        for cname, members in cls.items():
            if members:
                lines.append(f"class {','.join(members)} {cname};")
        return "\n".join(lines)

    def _dep_overview(self, graph: AssetGraph, names: dict[str, str], cap: int = 60) -> str | None:
        resolved = [e for e in graph.edges if e.resolved][:200]
        if not resolved:
            return None
        keys: list[str] = []
        seen: set[str] = set()
        for e in resolved:
            for k in (e.source_key, e.target_key):
                if k in names and k not in seen:
                    seen.add(k)
                    keys.append(k)
        keys = keys[:cap]
        idx = {k: f"a{i}" for i, k in enumerate(keys)}
        lines = ["flowchart LR"]
        for k in keys:
            lines.append(f'    {idx[k]}["{_mermaid_label(names[k])}"]')
        for e in resolved:
            if e.source_key in idx and e.target_key in idx:
                lines.append(f"    {idx[e.source_key]} --> {idx[e.target_key]}")
        return "\n".join(lines)

    def _write_external_stubs(
        self, graph: AssetGraph, names: dict[str, str], out: Path
    ) -> dict[str, str]:
        """A note per unresolved dependency so it still appears in the graph."""
        import collections

        ext_edges: dict[str, list] = collections.defaultdict(list)
        for e in graph.edges:
            if not e.resolved and e.target_key not in names:
                ext_edges[e.target_key].append(e)
        if not ext_edges:
            return {}

        folder = out / "_External"
        folder.mkdir(parents=True, exist_ok=True)
        used = {n.lower() for n in names.values()}
        ext: dict[str, str] = {}

        for tkey, edges in sorted(ext_edges.items()):
            label = next((e.target_name for e in edges if e.target_name), tkey)
            base = label
            if "/" in base or "\\" in base or ":" in base:
                base = re.split(r"[\\/]", base.split("?")[0].rstrip("/"))[-1] or base
            base = _sanitize(base) or "external"
            cand, i = base, 2
            while cand.lower() in used:
                cand = f"{base} (external {i})"
                i += 1
            used.add(cand.lower())
            ext[tkey] = cand

            kinds = ", ".join(sorted({e.kind for e in edges}))
            refby = sorted({names.get(e.source_key, e.source_key) for e in edges})
            doc = [
                "---",
                "tags: [cai/external, needs-review]",
                "---",
                f"# {label}",
                "",
                "> [!info] External / unresolved reference",
                "> Not an asset inside this export — shown so the dependency"
                " stays visible in the graph and canvas.",
                "",
                f"- relationship: {kinds}",
                f"- raw target: `{label}`",
                "",
                "## Referenced by",
                "",
                *[f"- [[{r}]]" for r in refby],
                "",
            ]
            (folder / f"{cand}.md").write_text("\n".join(doc), encoding="utf-8")
        return ext

    # --- write --------------------------------------------------------------

    def write(self, graph: AssetGraph, report: RunReport) -> None:
        out = self.config.output_dir
        out.mkdir(parents=True, exist_ok=True)
        names = self._assign_note_names(graph.assets)
        by_key = graph.by_key()

        by_guid_note = {a.guid: names[a.key] for a in graph.assets if a.guid}
        by_name_note: dict[str, str] = {}
        for a in graph.assets:
            for nm in (a.name, a.display_name):
                if nm:
                    by_name_note.setdefault(_norm(nm), names[a.key])

        ext_names = self._write_external_stubs(graph, names, out)
        link_name = {**names, **ext_names}

        asset_tmpl = self.env.get_template("asset.md.j2")
        note_rel: dict[str, str] = {}

        for a in graph.assets:
            note = names[a.key]
            folder = out / (a.project_path or Path(a.source_relpath).parent.as_posix() or ".")
            folder.mkdir(parents=True, exist_ok=True)
            note_rel[a.key] = (folder / f"{note}.md").relative_to(out).as_posix()

            uses = []
            for e in graph.uses.get(a.key, []):
                tgt = link_name.get(e.target_key)
                if tgt:
                    uses.append(f"{e.kind}: [[{tgt}]]")
                else:
                    uses.append(f"{e.kind}: {e.target_name or e.target_key}")
            used_by = [
                f"{e.kind}: [[{names[e.source_key]}]]"
                for e in graph.used_by.get(a.key, [])
                if e.source_key in names
            ]
            connectors = sorted(
                {
                    f"`{r.target_name}`{(' / ' + r.action) if r.action else ''}"
                    for r in a.references
                    if r.kind in ("connection", "service_connector")
                }
            )

            body = asset_tmpl.render(
                frontmatter=self._frontmatter(a),
                title=a.display_name or a.name,
                needs_review=a.needs_review,
                confidence=a.confidence,
                notes=a.notes,
                summary=a.static_summary,
                llm_narrative=a.llm_narrative,
                mermaid=self._mermaid_flow(a, by_guid_note, by_name_note),
                inputs=a.inputs,
                outputs=a.outputs,
                temp_fields=a.temp_fields,
                sql_blocks=a.sql_blocks,
                connectors=connectors,
                uses=uses,
                used_by=used_by,
                expressions=a.expressions[:20],
                config=a.config,
                sample_data=a.sample_data,
                include_sample_data=self.config.include_sample_data,
                raw_dump=a.raw_dump,
            )
            (folder / f"{note}.md").write_text(body, encoding="utf-8")

        # MOC pages
        moc_tmpl = self.env.get_template("moc.md.j2")
        by_type: dict[str, list[Asset]] = {}
        for a in graph.assets:
            by_type.setdefault(a.asset_type, []).append(a)
        for atype, group in by_type.items():
            title = _TYPE_TITLE.get(atype, atype.title())
            rows = sorted(
                (
                    {
                        "note": names[a.key],
                        "project": a.project_path or "",
                        "needs_review": a.needs_review,
                    }
                    for a in group
                ),
                key=lambda r: r["note"].lower(),
            )
            (out / f"_MOC {title}.md").write_text(
                moc_tmpl.render(title=title, count=len(group), rows=rows),
                encoding="utf-8",
            )

        # Home
        home_tmpl = self.env.get_template("home.md.j2")
        counts = sorted(
            (_TYPE_TITLE.get(t, t.title()), n) for t, n in report.counts_by_type.items()
        )
        confidence = sorted(report.confidence_buckets.items())
        needs_review = sorted(
            names[a.key] for a in graph.assets if a.needs_review
        )
        (out / "Home.md").write_text(
            home_tmpl.render(
                title="Informatica CAI — Documentation Home",
                total=report.total_assets,
                counts=counts,
                confidence=confidence,
                needs_review=needs_review,
                dep_mermaid=self._dep_overview(graph, names),
            ),
            encoding="utf-8",
        )

        self._write_canvas(graph, names, ext_names, note_rel, out)

    # --- Obsidian Canvas ----------------------------------------------------

    def _write_canvas(
        self,
        graph: AssetGraph,
        names: dict[str, str],
        ext_names: dict[str, str],
        note_rel: dict[str, str],
        out: Path,
    ) -> None:
        """Write _Dependencies.canvas (JSON Canvas) — a spatial dependency map."""
        import json

        # color presets (Obsidian canvas: "1".."6")
        color = {
            "process": "5", "service_connector": "4", "connection": "2",
            "process_object": "3", "schema": "6", "resource": "6",
            "deployment": "1", "project": "3", "external": "1",
        }
        type_order = [
            "process", "service_connector", "connection", "process_object",
            "schema", "resource", "deployment", "project", "external",
        ]

        # bucket node keys by column (type)
        col_keys: dict[str, list[str]] = {t: [] for t in type_order}
        atype = {a.key: a.asset_type for a in graph.assets}
        file_of: dict[str, str] = {}
        for a in graph.assets:
            col_keys.setdefault(a.asset_type, []).append(a.key)
            file_of[a.key] = note_rel.get(a.key, "")
        for tkey, nm in ext_names.items():
            col_keys["external"].append(tkey)
            file_of[tkey] = f"_External/{nm}.md"

        W, H, GAP_X, GAP_Y = 320, 64, 460, 96
        nodes, node_ids = [], {}
        col = 0
        for t in type_order:
            keys = sorted(col_keys.get(t, []), key=lambda k: file_of.get(k, ""))
            if not keys:
                continue
            for row, k in enumerate(keys):
                nid = f"n{len(nodes)}"
                node_ids[k] = nid
                f = file_of.get(k)
                base = {
                    "id": nid, "x": col * GAP_X, "y": row * GAP_Y,
                    "width": W, "height": H, "color": color.get(t, "0"),
                }
                if f:
                    base |= {"type": "file", "file": f}
                else:
                    base |= {"type": "text", "text": k}
                nodes.append(base)
            col += 1

        edges = []
        for i, e in enumerate(graph.edges):
            s, d = node_ids.get(e.source_key), node_ids.get(e.target_key)
            if s and d:
                edges.append({
                    "id": f"e{i}", "fromNode": s, "toNode": d,
                    "fromSide": "right", "toSide": "left",
                    "label": e.kind.replace("uses-", "").replace("calls-", ""),
                })

        canvas = {"nodes": nodes, "edges": edges}
        (out / "_Dependencies.canvas").write_text(
            json.dumps(canvas, indent=1), encoding="utf-8"
        )
