# Architecture

`cai-docs` is a seven-stage pipeline. Each stage is an isolated, independently
tested unit with a single responsibility; data flows one direction through plain
dataclasses (`models.py`), so any stage can be reasoned about or replaced alone.

```mermaid
flowchart TD
    Z["📦 Informatica CAI export<br/>(.zip or directory)"]:::input

    subgraph PIPE["cai-docs pipeline"]
        direction TB
        I["1 · ingest<br/>unzip (zip-slip safe),<br/>expand nested zips,<br/>walk → RawFiles"]
        P["2 · xmlmodel<br/>recovering XML parse +<br/>JSON sidecars → XmlDoc"]
        C["3 · classify<br/>weighted signals: MimeType,<br/>Entry root, filename/contrib<br/>→ type + confidence"]
        E["4 · extract<br/>identity, interface, flow graph,<br/>references, embedded SQL,<br/>expressions, raw-dump"]
        G["5 · graph<br/>resolve refs by GUID/name,<br/>reverse 'used-by',<br/>unresolved → external"]
        D["6 · describe<br/>deterministic summary +<br/>optional cached Claude prose<br/>(secrets/PII excluded)"]
        R["7 · render<br/>Jinja2 → markdown,<br/>Mermaid flow, wikilinks,<br/>folder mirroring"]
        I --> P --> C --> E --> G --> D --> R
    end

    Z --> I
    R --> V

    subgraph V["🗂️ Obsidian vault (output)"]
        direction TB
        AP["Asset pages<br/>frontmatter · summary ·<br/>Mermaid flow · SQL ·<br/>interface · Uses/Used-by"]
        MOC["Per-type MOC pages<br/>_MOC Processes …"]
        HM["Home.md<br/>counts · confidence ·<br/>needs-review · dep diagram"]
    end

    C -. "low confidence /<br/>unrecognized" .-> NR["⚠ flagged needs-review<br/>(never dropped)"]
    NR -.-> AP

    KEY["What it documents:<br/>processes • subprocesses •<br/>service connectors • app connections •<br/>process objects • guides • schemas<br/>— linked into a navigable graph"]:::note

    classDef input fill:#e3f2fd,stroke:#1565c0,color:#0d47a1
    classDef note fill:#fff8e1,stroke:#f9a825,color:#5d4037
    class Z input
    class KEY note
```

## Reading it

A CAI export goes in the top; seven stages transform it top-to-bottom; the
result is an Obsidian vault where every asset is a page, cross-linked via
`[[wikilinks]]` so the graph view becomes a map of the integration.

The dotted path is the **schema-adaptive fallback**: the engine is
profile-first (it knows the validated ActiveVOS `aetgt:getResponse → Item →
Entry → process` schema) but anything it cannot confidently classify is still
extracted best-effort and flagged `needs-review` — data is never silently
dropped.

## Stage reference

| # | Module | Input → Output | Responsibility |
|---|---|---|---|
| 1 | `ingest` | path → `RawFile[]` | Safe unzip, recursive nested-zip expansion, tree walk |
| 2 | `xmlmodel` | `RawFile` → `XmlDoc` | Recovering XML parse, JSON sidecars, namespace-agnostic helpers |
| 3 | `classify` | `XmlDoc` → type + confidence | Weighted signal scoring; unknowns flagged, not dropped |
| 4 | `extract` | `XmlDoc` → `Asset` | Metadata, interface, flow graph, references, SQL, raw-dump |
| 5 | `graph` | `Asset[]` → `AssetGraph` | Resolve references, reverse "used-by", external nodes |
| 6 | `describe` | `Asset` → summary/narrative | Deterministic summary; optional cached Claude prose |
| 7 | `render` | `AssetGraph` → vault | Jinja2 pages, Mermaid, wikilinks, MOCs, Home |

`cli` + `pipeline` wire the stages together and emit a run report.
