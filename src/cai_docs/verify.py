"""Post-render verification of generated Mermaid diagrams.

The renderer emits Mermaid flowcharts. A single malformed line makes Obsidian
refuse to render the whole diagram, so every run statically validates each
generated ```mermaid block against the failure classes that the real Mermaid
parser rejects (verified against mermaid 11.x):

  1. unquoted edge labels containing ( ) , $ ; : etc. -> "Parse error"
  2. unbalanced double quotes on a line
  3. an edge referencing a node id that is never defined
  4. empty node labels  ([""] / {""} / [[""]])
  5. missing/!=1 fenced ```mermaid ... ``` delimiters
  6. missing or unknown diagram header

These are static heuristics, not a full Mermaid parser, but they encode the
concrete breakages this engine has hit and guard against regressions.
"""

from __future__ import annotations

import re
from pathlib import Path

_HEADER_RE = re.compile(r"^(?:flowchart|graph)\s+(?:TD|TB|BT|RL|LR)\b")
# id followed by a shape opener:  n0["..."]  n3{"..."}  n9(["..."])  n8[["..."]]
_NODE_RE = re.compile(r"^([A-Za-z_][\w-]*)\s*(\(\[|\[\[|\[/|\[|\(|\{)")
# edge: src <op> [|label|] tgt   (covers -->  -.->  ---  ==>  --x  --o)
_EDGE_RE = re.compile(
    r"^([A-Za-z_][\w-]*)\s*"
    r"(?:-\.-|-\.->|--[xo]|--+>|--+|==+>)\s*"
    r"(\|[^|]*\|)?\s*"
    r"([A-Za-z_][\w-]*)\s*$"
)
_EMPTY_LABEL_RE = re.compile(r'(\[\[""\]\]|\["""\]|\["\"\]|\{""\}|\(\["\"\]\)|\[""\])')


def verify_mermaid(src: str) -> list[str]:
    """Return a list of human-readable problems for one diagram body."""
    issues: list[str] = []
    lines = [ln.rstrip() for ln in src.splitlines()]
    nonempty = [ln.strip() for ln in lines if ln.strip()]
    if not nonempty:
        return ["empty diagram"]
    if not _HEADER_RE.match(nonempty[0]):
        issues.append(f"bad/missing header: {nonempty[0][:40]!r}")

    defined: set[str] = set()
    edges: list[tuple[str, str]] = []
    edge_label_lines: list[str] = []

    for raw in nonempty[1:]:
        if raw.count('"') % 2:
            issues.append(f"unbalanced quotes: {raw[:60]!r}")
        if _EMPTY_LABEL_RE.search(raw):
            issues.append(f"empty node label: {raw[:60]!r}")

        if raw.startswith(("classDef ", "class ", "click ", "style ", "linkStyle ",
                           "subgraph", "end", "%%")):
            continue

        m_edge = _EDGE_RE.match(raw)
        if m_edge:
            src_id, label, tgt_id = m_edge.group(1), m_edge.group(2), m_edge.group(3)
            edges.append((src_id, tgt_id))
            if label is not None:
                edge_label_lines.append(raw)
                body = label[1:-1].strip()  # strip surrounding | |
                if not (len(body) >= 2 and body[0] == '"' and body[-1] == '"'):
                    issues.append(
                        f"unquoted edge label (Mermaid will fail): {raw[:70]!r}"
                    )
            continue

        m_node = _NODE_RE.match(raw)
        if m_node:
            defined.add(m_node.group(1))

    for s, t in edges:
        for nid in (s, t):
            if nid not in defined:
                issues.append(f"edge references undefined node {nid!r}")
    return issues


_FENCE_RE = re.compile(r"```mermaid\n(.*?)\n```", re.DOTALL)


def verify_file(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    opens = text.count("```mermaid")
    blocks = _FENCE_RE.findall(text)
    out: list[str] = []
    if opens != len(blocks):
        out.append(f"{path.name}: unclosed ```mermaid fence")
    for i, b in enumerate(blocks):
        for msg in verify_mermaid(b):
            out.append(f"{path.name} [block {i + 1}]: {msg}")
    return out


def verify_vault(out_dir: Path) -> tuple[int, list[str]]:
    """Scan every .md in the vault. Returns (block_count, issues)."""
    block_count = 0
    issues: list[str] = []
    for md in sorted(out_dir.rglob("*.md")):
        text = md.read_text(encoding="utf-8")
        if "```mermaid" not in text:
            continue
        block_count += len(_FENCE_RE.findall(text))
        issues.extend(verify_file(md))
    return block_count, issues
