# cai-docs

Generate an [Obsidian](https://obsidian.md) documentation vault from an
**Informatica CAI** (Cloud Application Integration / IDMC) repository export.

Point it at a GitHub zip dump (or an extracted folder) of a CAI project and it
discovers the assets, documents what each one does, and links them together so
the Obsidian graph view becomes a navigable map of your integration.

## What it produces

For every discovered asset:

- **Frontmatter** — type, GUID, project, version, publication status, author,
  source path, `cai/<type>` tags (plus `needs-review` when confidence is low).
- **Summary** — a deterministic description of the asset's logic; optionally an
  AI-written prose overview (see *LLM enrichment*).
- **Mermaid flowchart** (processes) — start/end, decisions, parallel blocks,
  subprocess and connector steps (hyperlinked), and fault/catch paths.
- **Interface** — inputs, outputs, temp fields.
- **SQL** — embedded queries reconstructed best-effort, with the raw expression.
- **Connector calls**, **Expressions**, **Configuration**.
- **Uses / Used by** — `[[wikilinks]]` to other assets (drives the graph view).
- **Raw structure appendix** — a flattened dump so nothing is lost.

Plus per-type Map-of-Content pages (`_MOC Processes.md`, …) and a `Home.md`
with counts, a confidence breakdown, the needs-review list, and a high-level
dependency diagram. Folders mirror the source repo structure.

## How it works

The engine is **schema-profile-first with an adaptive fallback**. It knows the
validated ActiveVOS repository export schema that CAI produces
(`aetgt:getResponse → types1:Item → types1:Entry → process`), and classifies by
weighted signals (MimeType, the Entry payload root, filename / contribution-id
suffixes). Anything it can't confidently classify is still documented and
flagged `needs-review` — data is never silently dropped.

Pipeline: `ingest → parse → classify → extract → graph → describe → render`.

See [`docs/architecture.md`](docs/architecture.md) for a diagram and per-stage
reference.

## Install

```bash
pip install -e .            # core (lxml, jinja2)
pip install -e ".[llm]"     # + Anthropic SDK for prose enrichment
pip install -e ".[dev]"     # + pytest
```

Requires Python ≥ 3.11.

## Usage

```bash
cai-docs <export.zip | export_dir> -o <vault_dir> [options]
```

Options:

| flag | default | meaning |
|---|---|---|
| `--no-llm` | LLM on if logged in | static extraction only |
| `--model` | `claude-opus-4-7` | Anthropic model id |
| `--max-workers` | `4` | LLM concurrency |
| `--confidence-threshold` | `0.45` | below this → `needs-review` |
| `--include-sample-data` | off | emit raw sample payloads (PII) |
| `--cache-dir` | `.cache/cai-docs` | LLM response cache |

Example:

```bash
cai-docs ./my-cai-export.zip -o ./CAI-Vault --no-llm
```

Open the output folder as an Obsidian vault.

## LLM enrichment (optional)

If you are **logged in to Claude** and `--no-llm` is not passed, each asset also
gets a short prose overview from Claude. Log in once with the Claude CLI:

```bash
claude login          # or: claude setup-token
```

`cai-docs` reuses that login (the OAuth credentials at
`~/.claude/.credentials.json`); no API key to manage. If you are not logged in
it prints a reminder and continues with static extraction only. An
`ANTHROPIC_API_KEY` environment variable is still honoured as a fallback.
Responses are content-hash cached so re-runs are cheap and deterministic.

**Privacy:** secrets in configuration are redacted and `sample-data` payloads
are never sent to the model. Raw sample payloads are kept out of the vault
unless you pass `--include-sample-data`.

## Development

```bash
pytest -q
```

Tests run against two real CAI process exports plus synthetic stand-ins for the
other asset types, an intentionally-unknown XML, and a nested zip.
