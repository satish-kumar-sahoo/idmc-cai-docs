"""Stage 2: parse RawFile bytes into an XmlDoc.

Uses a recovering parser so malformed exports still yield a usable tree.
Also exposes small namespace-agnostic helpers reused by classify/extract,
because Informatica assets use many shifting namespaces and we almost always
want to match on local-names.
"""

from __future__ import annotations

import json
from collections.abc import Iterator

from lxml import etree

from .models import RawFile, XmlDoc

_PARSER = etree.XMLParser(recover=True, huge_tree=True, resolve_entities=False)


def local(tag: object) -> str:
    """Local-name of an element tag or QName-ish string ('{ns}foo' -> 'foo')."""
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1]


def lname(el) -> str:
    return local(el.tag)


def children_local(el, name: str) -> list:
    return [c for c in el if isinstance(c.tag, str) and lname(c) == name]


def descendants_local(el, name: str) -> Iterator:
    for d in el.iter():
        if isinstance(d.tag, str) and lname(d) == name:
            yield d


def first_text(el, name: str, default: str = "") -> str:
    for c in children_local(el, name):
        return (c.text or "").strip() or default
    return default


def parse(raw: RawFile) -> XmlDoc:
    text = raw.data.decode("utf-8", errors="replace")
    doc = XmlDoc(relpath=raw.relpath, raw_text=text)

    if raw.ext == "json":
        try:
            doc.json_sidecar = json.loads(text)
        except (ValueError, TypeError) as exc:
            doc.parse_error = f"json: {exc}"
        return doc

    try:
        root = etree.fromstring(raw.data, _PARSER)
    except (etree.XMLSyntaxError, ValueError) as exc:
        doc.parse_error = f"xml: {exc}"
        return doc

    if root is None:
        doc.parse_error = "xml: empty or unrecoverable document"
        return doc

    doc.tree = root
    doc.root_localname = lname(root)
    doc.namespaces = {(k or ""): v for k, v in (root.nsmap or {}).items()}
    return doc


def parse_all(files: list[RawFile]) -> list[XmlDoc]:
    return [parse(f) for f in files]
