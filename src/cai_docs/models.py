"""Data structures passed between pipeline stages.

Each stage consumes and produces these plain dataclasses so stages stay
decoupled and independently testable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


# --- ingest -----------------------------------------------------------------


@dataclass
class RawFile:
    """A single file discovered inside the export (zip or directory)."""

    relpath: str  # POSIX-style path relative to the export root
    abs_path: Path
    ext: str  # lowercase extension without dot, e.g. "xml"
    data: bytes


# --- parse ------------------------------------------------------------------


@dataclass
class XmlDoc:
    """Parsed (or attempted) view of a RawFile."""

    relpath: str
    raw_text: str
    root_localname: str | None = None
    namespaces: dict[str, str] = field(default_factory=dict)
    tree: object | None = None  # lxml.etree._Element; typed loosely to avoid hard dep here
    json_sidecar: dict | None = None
    parse_error: str | None = None


# --- extract ----------------------------------------------------------------


@dataclass
class Field:
    """An input/output/temp parameter on a process."""

    name: str
    type: str = "string"
    required: bool = False
    description: str = ""
    initial_value: str | None = None


@dataclass
class Reference:
    """A pointer from one asset to another, discovered during extraction."""

    kind: str  # subprocess | connection | service_connector | catalog_resource | connector_hint
    raw: str
    target_guid: str | None = None
    target_name: str | None = None
    action: str | None = None
    context: str | None = None


@dataclass
class FlowNode:
    id: str
    kind: str  # start|end|assignment|service|subflow|eventContainer|container|jumpTo|throw|unknown
    title: str | None = None
    attrs: dict[str, str] = field(default_factory=dict)
    details: dict = field(default_factory=dict)


@dataclass
class FlowEdge:
    target: str
    source: str | None = None
    kind: str = "link"  # link | containerLink
    condition: str | None = None


@dataclass
class FlowGraph:
    nodes: list[FlowNode] = field(default_factory=list)
    edges: list[FlowEdge] = field(default_factory=list)
    start_id: str | None = None
    end_ids: list[str] = field(default_factory=list)


@dataclass
class SqlBlock:
    raw_expression: str
    reconstructed: str | None = None
    service_name: str | None = None
    connection: str | None = None
    context: str | None = None


@dataclass
class ExpressionItem:
    expression: str
    language: str = "XQuery"
    target: str | None = None  # the operation 'to=' or condition context
    context: str | None = None


@dataclass
class SampleData:
    name: str
    field_keys: list[str] = field(default_factory=list)
    raw_json: str = ""  # sensitive: never sent to LLM; rendered only with --include-sample-data
    created_by: str | None = None
    modified_by: str | None = None


@dataclass
class Asset:
    """The uniform record every asset is reduced to, regardless of source shape."""

    source_relpath: str
    asset_type: str = "unknown"
    confidence: float = 0.0
    classification_signals: list[str] = field(default_factory=list)
    needs_review: bool = False
    notes: list[str] = field(default_factory=list)

    # identity & metadata
    guid: str | None = None
    name: str = ""
    display_name: str | None = None
    entry_id: str | None = None
    published_contribution_id: str | None = None
    project_path: str | None = None  # derived folder path for vault mirroring
    description: str = ""
    version_label: str | None = None
    state: str | None = None
    publication_status: str | None = None
    created_by: str | None = None
    creation_date: str | None = None
    modified_by: str | None = None
    modification_date: str | None = None

    # interface
    inputs: list[Field] = field(default_factory=list)
    outputs: list[Field] = field(default_factory=list)
    temp_fields: list[Field] = field(default_factory=list)

    # behaviour
    flow: FlowGraph | None = None
    references: list[Reference] = field(default_factory=list)
    sql_blocks: list[SqlBlock] = field(default_factory=list)
    expressions: list[ExpressionItem] = field(default_factory=list)
    rest_trigger: bool = False
    config: dict[str, str] = field(default_factory=dict)
    sample_data: list[SampleData] = field(default_factory=list)

    # nothing-is-lost fallback: (xpath, value) of elements not otherwise consumed
    raw_dump: list[tuple[str, str]] = field(default_factory=list)

    # filled by describe
    static_summary: str = ""
    llm_narrative: str | None = None

    @property
    def key(self) -> str:
        """Stable identity used for graph indexing and wikilinks."""
        return self.guid or self.name or self.source_relpath


# --- graph ------------------------------------------------------------------


@dataclass
class Edge:
    source_key: str
    target_key: str
    kind: str  # calls-subprocess | uses-connection | uses-service-connector | references-resource
    target_name: str | None = None
    resolved: bool = True


@dataclass
class AssetGraph:
    assets: list[Asset] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    # key -> list of edges
    uses: dict[str, list[Edge]] = field(default_factory=dict)
    used_by: dict[str, list[Edge]] = field(default_factory=dict)
    unresolved: list[Reference] = field(default_factory=list)

    def by_key(self) -> dict[str, Asset]:
        return {a.key: a for a in self.assets}


# --- reporting --------------------------------------------------------------


@dataclass
class RunReport:
    total_assets: int = 0
    counts_by_type: dict[str, int] = field(default_factory=dict)
    confidence_buckets: dict[str, int] = field(default_factory=dict)
    needs_review_count: int = 0
    unresolved_references: int = 0
    llm_calls: int = 0
    llm_cache_hits: int = 0
    files_seen: int = 0
    files_parsed: int = 0

    def render(self) -> str:
        lines = ["cai-docs run report", "-" * 40]
        lines.append(f"files seen / parsed : {self.files_seen} / {self.files_parsed}")
        lines.append(f"assets              : {self.total_assets}")
        for t, n in sorted(self.counts_by_type.items()):
            lines.append(f"  {t:<22}: {n}")
        lines.append(f"needs-review        : {self.needs_review_count}")
        lines.append(f"confidence          : {dict(sorted(self.confidence_buckets.items()))}")
        lines.append(f"unresolved refs     : {self.unresolved_references}")
        lines.append(f"llm calls / cached  : {self.llm_calls} / {self.llm_cache_hits}")
        return "\n".join(lines)
