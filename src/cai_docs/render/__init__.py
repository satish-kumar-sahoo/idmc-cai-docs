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

    def _assign_note_names(self, assets: list[Asset]) -> dict[str, str]:
        names: dict[str, str] = {}
        used: set[str] = set()
        for a in assets:
            base = _sanitize(a.display_name or a.name or a.key)
            candidate = base
            n = 2
            while candidate.lower() in used:
                suffix = (a.guid or "")[:6] or str(n)
                candidate = f"{base} ({suffix})"
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
        for node in a.flow.nodes:
            mi = mid[node.id]
            label = _mermaid_label(node.title or node.kind)
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
        for e in a.flow.edges:
            if e.source in mid and e.target in mid:
                if e.condition:
                    lines.append(
                        f'    {mid[e.source]} -->|{_edge_label(e.condition)}| {mid[e.target]}'
                    )
                else:
                    lines.append(f"    {mid[e.source]} --> {mid[e.target]}")
        lines.extend(f"    {c}" for c in clicks)
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

        asset_tmpl = self.env.get_template("asset.md.j2")

        for a in graph.assets:
            note = names[a.key]
            folder = out / (a.project_path or Path(a.source_relpath).parent.as_posix() or ".")
            folder.mkdir(parents=True, exist_ok=True)

            uses = []
            for e in graph.uses.get(a.key, []):
                if e.resolved and e.target_key in names:
                    uses.append(f"{e.kind}: [[{names[e.target_key]}]]")
                else:
                    uses.append(f"{e.kind}: {e.target_name or e.target_key} _(unresolved)_")
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
