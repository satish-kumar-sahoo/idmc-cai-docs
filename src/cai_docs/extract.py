"""Stage 4: reduce an XmlDoc into a uniform Asset.

Profile-driven for the validated ActiveVOS process schema, with generic
fallbacks for everything else. Every sub-extraction is defensive: a failure
adds a note and degrades gracefully instead of raising, so an asset is always
produced (nothing is silently dropped).
"""

from __future__ import annotations

import json
import re

from .models import (
    Asset,
    ConnectorAction,
    ExpressionItem,
    Field,
    FlowEdge,
    FlowGraph,
    FlowNode,
    Reference,
    SampleData,
    SqlBlock,
    XmlDoc,
)
from .xmlmodel import children_local, descendants_local, first_text, lname

_FLOW_NODE_KINDS = {
    "start",
    "end",
    "assignment",
    "service",
    "subflow",
    "container",
    "jumpTo",
    "throw",
}

_SQL_KEYWORDS = re.compile(
    r"\b(SELECT|INSERT|UPDATE|DELETE|MERGE|WITH|CALL|EXEC|UPSERT)\b", re.IGNORECASE
)
_CATALOG_RE = re.compile(r"getCatalogResource\(\s*[\"']([^\"']+)[\"']")
_SECRET_KEY = re.compile(r"(secret|password|passwd|token|apikey|api_key|client_secret|key)$", re.I)
# attributes whose *values* are live credentials / PII test fixtures, never emitted
_SECRET_ATTRS = {"testwith", "samplevalue", "defaultvalue"}
_REDACTED = "<redacted>"
# value-shaped secret detection: JWT / bearer token. Deliberately narrow so
# legitimate config (hostnames, JDBC URLs, class names) is never mangled —
# key-name and encrypted/masked flags handle the rest.
_JWT_RE = re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}")


def _looks_secret(value: str | None) -> bool:
    """True only if a value is unambiguously a token (JWT or Bearer header)."""
    if not value:
        return False
    v = value.strip()
    return bool(_JWT_RE.search(v)) or v[:7].lower() == "bearer "


def _safe_value(name: str | None, value: str | None) -> str:
    if value and (_SECRET_KEY.search(name or "") or _looks_secret(value)):
        return _REDACTED
    return value or ""


# Platform / engine namespaces that are not project dependencies.
_PLATFORM_MARKERS = (
    "schemas.active-endpoints.com",
    "active-endpoints.com",
    "activebpel.org",
    "docs.oasis-open.org",
    "xmlsoap.org",
    "w3.org",
    "activevos",
)


def _is_platform_ref(value: str | None) -> bool:
    if not value:
        return False
    v = value.lower()
    return any(m in v for m in _PLATFORM_MARKERS)


def _attr(el, name: str, default: str = "") -> str:
    for k, v in el.attrib.items():
        if k.rsplit("}", 1)[-1] == name:
            return v
    return default


def _title(el) -> str | None:
    t = first_text(el, "title")
    return t or None


def _text_or_expr(el) -> tuple[str, str | None]:
    """Return (value_text, expr_language). value is element text or its <expression>."""
    exprs = children_local(el, "expression")
    if exprs:
        e = exprs[0]
        return (e.text or "").strip(), _attr(e, "language", "XQuery")
    return (el.text or "").strip(), None


# --- metadata ---------------------------------------------------------------


def _item(doc: XmlDoc):
    if doc.tree is None:
        return None
    for item in children_local(doc.tree, "Item"):
        return item
    # bare (non-enveloped) export: treat the root as its own "item-less" payload
    return None


def _payload(doc: XmlDoc, item):
    if item is not None:
        for entry in children_local(item, "Entry"):
            for child in entry:
                if isinstance(child.tag, str):
                    return child
    return doc.tree if doc.tree is not None and item is None else None


def _project_path(contrib: str, fallback: str) -> str:
    # "project:/spi.createMultipleIdentifier/createMultipleIdentifier.pd.xml"
    if contrib:
        path = contrib.split("project:/", 1)[-1]
        folder = path.rsplit("/", 1)[0] if "/" in path else ""
        if folder:
            return folder
    if "/" in fallback:
        return fallback.rsplit("/", 1)[0]
    return ""


def _fields(container, child_name: str) -> list[Field]:
    out: list[Field] = []
    if container is None:
        return out
    for f in children_local(container, child_name):
        opts = {}
        for ob in children_local(f, "options"):
            for o in children_local(ob, "option"):
                opts[_attr(o, "name")] = (o.text or "").strip()
        required = _attr(f, "required", "").lower() == "true" or opts.get(
            "required", ""
        ).lower() == "true"
        fname = _attr(f, "name") or first_text(f, "name")
        iv = opts.get("initialvalue")
        if iv and (_SECRET_KEY.search(fname) or _looks_secret(iv)):
            iv = _REDACTED
        out.append(
            Field(
                name=fname,
                type=_attr(f, "type", "string"),
                required=required,
                description=_attr(f, "description", ""),
                initial_value=iv,
            )
        )
    return out


# --- flow graph -------------------------------------------------------------


def _condition_text(link) -> str | None:
    conds = children_local(link, "condition")
    if not conds:
        return None
    cond = conds[0]
    funcs = list(descendants_local(cond, "function"))
    if funcs:
        fn = funcs[0]
        args = []
        for a in descendants_local(fn, "arg"):
            args.append((a.text or _attr(a, "name") or "").strip())
        return f"{_attr(fn, 'name')}({', '.join(x for x in args if x)})"
    txt = " ".join((cond.text or "").split())
    return txt or "condition"


def _id_of(el) -> str:
    return _attr(el, "id") if el is not None and isinstance(el.tag, str) else ""


def _primary_descendant(el, node_ids: set[str]) -> str | None:
    """First executable node id inside a wrapper (flow/eventContainer/...)."""
    for d in el.iter():
        if d is el or not isinstance(d.tag, str):
            continue
        if lname(d) in _FLOW_NODE_KINDS and _attr(d, "id") in node_ids:
            return _attr(d, "id")
    return None


def _representative(el, node_ids: set[str]) -> str | None:
    """Map any element to the flow node that best represents it.

    A real node maps to itself; a wrapper (``flow``/``eventContainer``) maps to
    its first executable child so links that point at or originate from wrappers
    still connect real steps.
    """
    if el is None or not isinstance(el.tag, str):
        return None
    if lname(el) in _FLOW_NODE_KINDS and _attr(el, "id") in node_ids:
        return _attr(el, "id")
    return _primary_descendant(el, node_ids)


def _nearest_with_id(el):
    node = el.getparent()
    while node is not None:
        if isinstance(node.tag, str) and _attr(node, "id"):
            return node
        node = node.getparent()
    return None


def _build_flow(flow_el) -> FlowGraph:
    g = FlowGraph()
    if flow_el is None:
        return g

    id_map: dict[str, object] = {}
    for el in flow_el.iter():
        if not isinstance(el.tag, str):
            continue
        eid = _attr(el, "id")
        if eid:
            id_map.setdefault(eid, el)
        kind = lname(el)
        if kind in _FLOW_NODE_KINDS and eid:
            node = FlowNode(id=eid, kind=kind, title=_title(el))
            if kind == "container":
                node.attrs["type"] = _attr(el, "type", "exclusive")
            elif kind == "service":
                node.details["serviceName"] = first_text(el, "serviceName")
                node.details["serviceGUID"] = first_text(el, "serviceGUID")
            elif kind == "subflow":
                node.details["subflowGUID"] = first_text(el, "subflowGUID")
                node.details["subflowPath"] = first_text(el, "subflowPath")
            elif kind == "assignment":
                node.details["operations"] = [
                    {"to": _attr(op, "to"), "value": _text_or_expr(op)[0]}
                    for op in children_local(el, "operation")
                ]
            elif kind == "throw":
                node.details["title"] = _title(el) or ""
            if kind == "start":
                g.start_id = eid
            if kind == "end":
                g.end_ids.append(eid)
            g.nodes.append(node)

    node_ids = {n.id for n in g.nodes}
    seen: set[tuple] = set()

    def add(src: str | None, tgt: str | None, kind: str, cond: str | None):
        if not src or not tgt or src == tgt:
            return
        sig = (src, tgt, kind, cond)
        if sig in seen:
            return
        seen.add(sig)
        g.edges.append(FlowEdge(target=tgt, source=src, kind=kind, condition=cond))

    for link in descendants_local(flow_el, "link"):
        target_raw = _attr(link, "targetId")
        if not target_raw:
            continue
        tgt = _representative(id_map.get(target_raw), node_ids)
        if tgt is None and target_raw in node_ids:
            tgt = target_raw
        src = _representative(_nearest_with_id(link), node_ids)
        is_catch = lname(link.getparent()) == "catch" if link.getparent() is not None else False
        add(src, tgt, "link", _condition_text(link))
        if is_catch:
            continue

    for catch in descendants_local(flow_el, "catch"):
        owner = _nearest_with_id(catch)
        src = _representative(owner, node_ids)
        fault = _attr(catch, "faultField") or "error"
        for link in descendants_local(catch, "link"):
            tgt_raw = _attr(link, "targetId")
            tgt = _representative(id_map.get(tgt_raw), node_ids)
            if tgt is None and tgt_raw in node_ids:
                tgt = tgt_raw
            add(src, tgt, "catch", f"fault: {fault}")
    return g


# --- references / sql / expressions -----------------------------------------


def _parse_service_name(service_name: str) -> tuple[str, str | None, str | None]:
    """'AppConnection-OT-Submit-Consent:Action' -> (kind, connection, action)."""
    if not service_name:
        return "connection", None, None
    left, _, action = service_name.partition(":")
    kind_token, _, conn = left.partition("-")
    kind = "service_connector" if "serviceconnector" in kind_token.lower() else "connection"
    return kind, (conn or left) or None, (action or None)


def _is_sql_service(service_name: str, conn: str | None) -> bool:
    s = service_name.lower()
    c = (conn or "").lower()
    return (
        "sql" in s
        or "sql db query" in s
        or "sql" in c
        or "das" in c
        or s.endswith(":sql db query execution")
    )


def _reconstruct_sql(expr: str) -> str | None:
    if not expr or not _SQL_KEYWORDS.search(expr):
        return None
    s = expr
    s = s.replace('||"\'"||', "'").replace('"\'"', "'")
    s = s.replace("||", " ")
    s = re.sub(r"\$[A-Za-z_][\w.]*\.([A-Za-z_]\w*)", r":\1", s)
    s = s.replace("'' ", "''").strip()
    if s.startswith("'") and s.endswith("'"):
        s = s[1:-1]
    s = re.sub(r"\s+", " ", s).strip()
    return s or None


def _collect_references(payload) -> list[Reference]:
    refs: list[Reference] = []
    seen: set[tuple] = set()

    def add(ref: Reference):
        sig = (ref.kind, ref.target_guid, ref.target_name, ref.action)
        if sig not in seen:
            seen.add(sig)
            refs.append(ref)

    for sf in descendants_local(payload, "subflow"):
        guid = first_text(sf, "subflowGUID") or None
        path = first_text(sf, "subflowPath") or None
        if guid or path:
            add(
                Reference(
                    kind="subprocess",
                    raw=path or guid or "",
                    target_guid=guid,
                    target_name=path,
                    context=_title(sf),
                )
            )
    for sv in descendants_local(payload, "service"):
        sname = first_text(sv, "serviceName")
        sguid = first_text(sv, "serviceGUID") or None
        kind, conn, action = _parse_service_name(sname)
        add(
            Reference(
                kind="service_connector" if kind == "service_connector" else "connection",
                raw=sname,
                target_guid=sguid,
                target_name=conn,
                action=action,
                context=_title(sv),
            )
        )
    # connector-name hints in tempFields initial values
    for f in descendants_local(payload, "field"):
        for ob in children_local(f, "options"):
            for o in children_local(ob, "option"):
                if _attr(o, "name") == "initialvalue":
                    v = (o.text or "").strip()
                    if re.match(r"(?i)(app)?connection-|serviceconnector[-_]", v):
                        add(
                            Reference(
                                kind="connector_hint",
                                raw=v,
                                target_name=v,
                                context=f"tempField {_attr(f, 'name')}",
                            )
                        )
    # catalog resource references inside any expression / text
    for ex in descendants_local(payload, "expression"):
        for m in _CATALOG_RE.finditer(ex.text or ""):
            if _is_platform_ref(m.group(1)):
                continue
            add(
                Reference(
                    kind="catalog_resource",
                    raw=m.group(1),
                    target_name=m.group(1),
                    context="getCatalogResource",
                )
            )
    # generic: referenced* elements (connections referencing service connectors, etc.)
    for el in payload.iter():
        if isinstance(el.tag, str) and lname(el).lower().startswith("referenced"):
            val = (el.text or "").strip()
            if val:
                add(
                    Reference(
                        kind="service_connector",
                        raw=val,
                        target_name=val,
                        context=lname(el),
                    )
                )
    return refs


def _collect_sql(payload) -> list[SqlBlock]:
    blocks: list[SqlBlock] = []
    for sv in descendants_local(payload, "service"):
        sname = first_text(sv, "serviceName")
        _kind, conn, _action = _parse_service_name(sname)
        if not _is_sql_service(sname, conn):
            continue
        for si in descendants_local(sv, "serviceInput"):
            for p in children_local(si, "parameter"):
                if _attr(p, "name").lower() in {"query", "sql", "statement"}:
                    raw, _lang = _text_or_expr(p)
                    if raw:
                        blocks.append(
                            SqlBlock(
                                raw_expression=raw,
                                reconstructed=_reconstruct_sql(raw),
                                service_name=sname,
                                connection=conn,
                                context=_title(sv),
                            )
                        )
    return blocks


def _ns_blob(doc: XmlDoc, payload) -> str:
    nss = list(doc.namespaces.values())
    try:
        nss += list((payload.nsmap or {}).values())
    except AttributeError:
        pass
    return " ".join(u for u in nss if u).lower()


def _collect_bpel_refs(payload) -> list[Reference]:
    """Light BPEL dependency extraction: imports, partner links, invokes."""
    refs: list[Reference] = []
    seen: set[tuple] = set()

    def add(kind, raw, name=None, action=None, ctx=None):
        sig = (kind, raw, name, action)
        if raw and sig not in seen:
            seen.add(sig)
            refs.append(
                Reference(kind=kind, raw=raw, target_name=name, action=action, context=ctx)
            )

    for imp in descendants_local(payload, "import"):
        loc = _attr(imp, "location") or _attr(imp, "namespace")
        if _is_platform_ref(loc):
            continue
        add("catalog_resource", loc, loc, ctx=f"bpel import ({_attr(imp, 'importType') or 'wsdl'})")
    self_name = (_attr(payload, "name") or "").lower()
    invoked = {_attr(i, "partnerLink") for i in descendants_local(payload, "invoke")}
    for pl in descendants_local(payload, "partnerLink"):
        nm = _attr(pl, "name")
        # the BPEL's own entry point (myRole / not invoked) is not a dependency
        if not nm or nm.lower() == self_name or nm not in invoked:
            continue
        add("connection", nm, nm, ctx="bpel partnerLink")
    for inv in descendants_local(payload, "invoke"):
        pl = _attr(inv, "partnerLink")
        add("connection", pl or _attr(inv, "name"), pl, _attr(inv, "operation"), "bpel invoke")
    return refs


def _collect_expressions(payload) -> list[ExpressionItem]:
    out: list[ExpressionItem] = []
    for ex in descendants_local(payload, "expression"):
        txt = (ex.text or "").strip()
        if not txt:
            continue
        parent = ex.getparent()
        target = _attr(parent, "to") if parent is not None else None
        ctx = lname(parent) if parent is not None else None
        out.append(
            ExpressionItem(
                expression=txt,
                language=_attr(ex, "language", "XQuery"),
                target=target or None,
                context=ctx,
            )
        )
    return out


def _collect_sample_data(item) -> list[SampleData]:
    out: list[SampleData] = []
    if item is None:
        return out
    for sds in descendants_local(item, "sample-data-sets"):
        for sd in children_local(sds, "SampleData"):
            data_text = first_text(sd, "Data")
            keys: list[str] = []
            try:
                parsed = json.loads(data_text)
                if isinstance(parsed, dict):
                    keys = list(parsed.keys())
            except (ValueError, TypeError):
                pass
            out.append(
                SampleData(
                    name=first_text(sd, "Name"),
                    field_keys=keys,
                    raw_json=data_text,
                    created_by=first_text(sd, "CreatedBy") or None,
                    modified_by=first_text(sd, "ModifiedBy") or None,
                )
            )
    return out


def _attr_value(el) -> str:
    """Attribute value, redacted when the source marks it secret/encrypted."""
    enc = (_attr(el, "encrypted") or _attr(el, "masked")).lower() == "true"
    name = _attr(el, "name")
    val = _attr(el, "value")
    if enc:
        return _REDACTED
    return _safe_value(name, val)


def _read_attributes(container, prefix: str, into: dict[str, str]) -> None:
    """Flatten <attributes>/<attribute name= value=> (and nested) into a dict."""
    if container is None:
        return
    for attrs in children_local(container, "attributes"):
        for at in children_local(attrs, "attribute"):
            nm = _attr(at, "name")
            if not nm:
                continue
            key = f"{prefix}{nm}" if prefix else nm
            into[key] = _attr_value(at) or "(empty)"
            for oth in children_local(at, "otherAttributes"):
                _read_attributes(oth, f"{key}.", into)


_AGENT_TAG_RE = re.compile(r"\.agent:([^\s,;<]+)")


def _runtime_from_agent_tag(text: str | None) -> tuple[str, str | None] | None:
    """`.agent:TME_AWS_LZ_DEV` tag -> ("Secure Agent", "TME_AWS_LZ_DEV")."""
    if not text:
        return None
    m = _AGENT_TAG_RE.search(text)
    if m:
        return "Secure Agent", m.group(1)
    return None


def _extract_connection(payload, asset: Asset) -> None:
    """App/data connection: parameters + where it executes."""
    jc = next(iter(descendants_local(payload, "javaConnector")), None)
    agent = first_text(payload, "agent") or None
    agent_only = jc is not None and _attr(jc, "agentOnly").lower() == "true"
    cloud_only = jc is not None and _attr(jc, "cloudOnly").lower() == "true"
    if agent_only:
        asset.runtime, asset.runtime_detail = "Secure Agent", agent
    elif cloud_only:
        asset.runtime = "Cloud"
    elif agent:
        # configured against a specific Secure Agent group -> runs there
        asset.runtime, asset.runtime_detail = "Secure Agent", agent
    else:
        asset.runtime = "Cloud"

    cfg: dict[str, str] = {}
    _read_attributes(payload, "", cfg)
    # older/DAS form: <properties>/<property name=>value
    for props in children_local(payload, "properties"):
        for p in children_local(props, "property"):
            nm = _attr(p, "name")
            if nm:
                cfg[nm] = (
                    _REDACTED if _SECRET_KEY.search(nm) else (p.text or "").strip()
                ) or "(empty)"
    for con in descendants_local(payload, "consumer"):
        cname = _attr(con, "name") or _attr(con, "typeName") or "consumer"
        _read_attributes(con, f"consumer:{cname}.", cfg)
    for jc in descendants_local(payload, "javaConnector"):
        if _attr(jc, "type"):
            cfg["connector.type"] = _attr(jc, "type")
        if _attr(jc, "plugin"):
            cfg["connector.plugin"] = _attr(jc, "plugin")
    agent = first_text(payload, "agent")
    if agent:
        cfg["agent"] = agent
    rsc = first_text(payload, "referencedServiceConnector")
    if rsc:
        cfg["referenced service connector"] = rsc
    asset.config.update(cfg)


def _connector_fields(container, child: str) -> list[Field]:
    out: list[Field] = []
    if container is None:
        return out
    for p in children_local(container, child):
        out.append(
            Field(
                name=_attr(p, "name") or _attr(p, "label"),
                type=_attr(p, "type", "string"),
                required=_attr(p, "required", "").lower() == "true",
                description=_attr(p, "description", "") or _attr(p, "label", ""),
            )
        )
    return out


def _extract_service_connector(payload, asset: Asset) -> None:
    """Service (business) connector: connection-level attrs + per-action ops."""
    asset.runtime = (
        "Secure Agent"
        if _attr(payload, "agentOnly").lower() == "true"
        else "Cloud"
    )
    if _attr(payload, "plugin"):
        asset.config["connector.plugin"] = _attr(payload, "plugin")
    for ca in children_local(payload, "connectionAttributes"):
        for cattr in children_local(ca, "connectionAttribute"):
            asset.inputs.append(
                Field(
                    name=_attr(cattr, "name") or _attr(cattr, "label"),
                    type=_attr(cattr, "type", "string"),
                    required=_attr(cattr, "required", "").lower() == "true",
                    description=_attr(cattr, "description", "")
                    or _attr(cattr, "label", ""),
                )
            )
    for group in children_local(payload, "actions") + children_local(
        payload, "dasActions"
    ):
        for act in children_local(group, "action"):
            ca = ConnectorAction(
                name=_attr(act, "name"),
                label=_attr(act, "label") or _attr(act, "name"),
                description=first_text(act, "description"),
            )
            inp = next(iter(children_local(act, "input")), None)
            outp = next(iter(children_local(act, "output")), None)
            ca.inputs = _connector_fields(inp, "parameter")
            ca.outputs = _connector_fields(outp, "field")
            binding = next(iter(children_local(act, "binding")), None)
            if binding is not None:
                rest = next(iter(children_local(binding, "restSimpleBinding")), None)
                sqlst = next(iter(children_local(binding, "sqlStatement")), None)
                if rest is not None:
                    ca.kind = "rest"
                    ca.verb = _attr(rest, "verb") or None
                    ca.url = _attr(rest, "url") or None
                    for hs in children_local(rest, "httpHeaders"):
                        for h in children_local(hs, "header"):
                            val = (h.text or "").strip()
                            nm = _attr(h, "name")
                            if _SECRET_KEY.search(nm) or "authorization" in nm.lower():
                                val = _REDACTED
                            ca.headers.append(f"{nm}: {val}".strip(": "))
                elif sqlst is not None:
                    ca.kind = "sql"
                    ca.sql = first_text(sqlst, "statement") or None
            asset.connector_actions.append(ca)


def _raw_dump(payload, cap: int = 400) -> list[tuple[str, str]]:
    dump: list[tuple[str, str]] = []
    if payload is None:
        return dump
    for el in payload.iter():
        if not isinstance(el.tag, str):
            continue
        text = (el.text or "").strip()
        if _looks_secret(text):
            text = _REDACTED
        secret_val = (
            (_attr(el, "encrypted") or _attr(el, "masked")).lower() == "true"
        )
        parts = []
        for k, v in el.attrib.items():
            kn = k.rsplit("}", 1)[-1]
            kl = kn.lower()
            if (
                kl in _SECRET_ATTRS
                or _SECRET_KEY.search(kn)
                or (secret_val and kl == "value")
                or _looks_secret(v)
            ):
                v = _REDACTED
            parts.append(f"{kn}={v}")
        attrs = " ".join(parts)
        if not text and not attrs:
            continue
        path = lname(el)
        p = el.getparent()
        depth = 0
        while p is not None and depth < 6:
            path = f"{lname(p)}/{path}"
            p = p.getparent()
            depth += 1
        dump.append((path, (text or attrs)[:300]))
        if len(dump) >= cap:
            dump.append(("...", f"(truncated at {cap} entries)"))
            break
    return dump


# --- entry point ------------------------------------------------------------


def merge_pdd(asset: Asset, pdd_doc: XmlDoc) -> None:
    """Merge a paired .pdd deploy descriptor into its .bpel process asset."""
    root = pdd_doc.tree
    if root is None:
        return
    asset.config["deploy.location"] = _attr(root, "location")
    asset.config["deploy.platform"] = _attr(root, "platform")
    for pl in descendants_local(root, "partnerLink"):
        name = _attr(pl, "name")
        roles = []
        for r in pl:
            if isinstance(r.tag, str) and lname(r) in ("myRole", "partnerRole"):
                svc = _attr(r, "service")
                binding = _attr(r, "binding") or _attr(r, "invokeHandler")
                roles.append(
                    f"{lname(r)}({svc or binding or 'n/a'})"
                )
                # partnerRole = an outbound dependency; myRole = own entry point
                if lname(r) == "partnerRole" and svc and _norm_self(svc, asset):
                    asset.references.append(
                        Reference(
                            kind="connection", raw=svc, target_name=svc,
                            context=f"pdd partnerLink {name}",
                        )
                    )
        if name:
            asset.config[f"deploy.partner.{name}"] = ", ".join(roles) or "n/a"
    for w in descendants_local(root, "wsdl"):
        loc = _attr(w, "location") or _attr(w, "namespace")
        if loc and not _is_platform_ref(loc):
            asset.references.append(
                Reference(
                    kind="catalog_resource", raw=loc, target_name=loc,
                    context="pdd reference",
                )
            )
    asset.notes.append("deployment descriptor merged (.pdd)")


def _norm_self(svc: str, asset: Asset) -> bool:
    """True if svc is a real outbound dep (not the process's own service name)."""
    return svc.strip().lower() not in {
        (asset.name or "").lower(),
        (asset.display_name or "").lower(),
    }


def extract(
    doc: XmlDoc,
    asset_type: str,
    confidence: float,
    signals: list[str],
    threshold: float = 0.45,
) -> Asset:
    stem = doc.relpath.rsplit("/", 1)[-1].lstrip(".")
    for suf in (".bpel", ".pdd", ".wsdl", ".xsd", ".xml", ".json"):
        if stem.lower().endswith(suf):
            stem = stem[: -len(suf)]
            break
    for infix in (
        ".PROCESS_OBJECT", ".AI_SERVICE_CONNECTOR", ".AI_CONNECTION",
        ".PROCESS", ".SERVICECONNECTOR", ".CONNECTION", ".GUIDE",
    ):
        if stem.upper().endswith(infix):
            stem = stem[: -len(infix)]
            break

    asset = Asset(
        source_relpath=doc.relpath,
        asset_type=asset_type,
        confidence=confidence,
        classification_signals=signals,
        name=stem,
    )
    asset.needs_review = (
        asset_type == "unknown"
        or confidence < threshold
        or doc.parse_error is not None
    )

    if doc.parse_error:
        asset.notes.append(f"parse error: {doc.parse_error}")
        return asset
    if doc.tree is None:
        asset.notes.append("no parseable XML tree")
        return asset

    try:
        item = _item(doc)
        if item is not None:
            asset.entry_id = first_text(item, "EntryId") or None
            asset.name = first_text(item, "Name") or asset.name
            asset.display_name = first_text(item, "DisplayName") or None
            asset.description = first_text(item, "Description")
            asset.version_label = first_text(item, "VersionLabel") or None
            asset.state = first_text(item, "State") or None
            asset.publication_status = first_text(item, "PublicationStatus") or None
            asset.created_by = first_text(item, "CreatedBy") or None
            asset.creation_date = first_text(item, "CreationDate") or None
            asset.modified_by = first_text(item, "ModifiedBy") or None
            asset.modification_date = first_text(item, "ModificationDate") or None
            asset.published_contribution_id = (
                first_text(item, "PublishedContributionId") or None
            )
            asset.guid = first_text(item, "GUID") or None
            rt = _runtime_from_agent_tag(first_text(item, "Tags"))
            if rt:
                asset.runtime, asset.runtime_detail = rt

        payload = _payload(doc, item)
        if payload is not None:
            asset.guid = asset.guid or _attr(payload, "GUID") or None
            asset.display_name = asset.display_name or _attr(payload, "displayName") or None
            if not asset.description:
                asset.description = first_text(payload, "description")

            asset.references = _collect_references(payload)
            asset.expressions = _collect_expressions(payload)
            asset.sql_blocks = _collect_sql(payload)

            root_lname = lname(payload).lower()
            if root_lname == "connection" or asset_type == "connection":
                _extract_connection(payload, asset)
            elif root_lname == "businessconnector" or asset_type == "service_connector":
                _extract_service_connector(payload, asset)

            ns_blob = _ns_blob(doc, payload)
            is_process_root = lname(payload).lower() == "process"
            is_bpel = is_process_root and "wsbpel" in ns_blob
            is_screenflow = is_process_root and not is_bpel and (
                "avosscreenflow" in ns_blob
                or next(iter(children_local(payload, "flow")), None) is not None
            )

            if is_screenflow:
                inp = next(iter(children_local(payload, "input")), None)
                outp = next(iter(children_local(payload, "output")), None)
                tmp = next(iter(children_local(payload, "tempFields")), None)
                asset.inputs = _fields(inp, "parameter")
                asset.outputs = _fields(outp, "field")
                asset.temp_fields = _fields(tmp, "field")
                asset.rest_trigger = bool(list(descendants_local(payload, "rest")))
                for nv in descendants_local(payload, "nvpair"):
                    asset.config[_attr(nv, "name")] = (nv.text or "").strip()
                flow_el = next(iter(children_local(payload, "flow")), None)
                asset.flow = _build_flow(flow_el)
            elif is_bpel:
                asset.references.extend(_collect_bpel_refs(payload))
                asset.rest_trigger = any(
                    _attr(r, "createInstance", "").lower() == "yes"
                    for r in descendants_local(payload, "receive")
                )
                asset.notes.append("BPEL process (flow diagram not extracted)")

            asset.sample_data = _collect_sample_data(item)
            asset.raw_dump = _raw_dump(payload)

        asset.project_path = _project_path(
            asset.published_contribution_id or "", doc.relpath
        )
    except Exception as exc:  # defensive: never fail the pipeline on one asset
        asset.needs_review = True
        asset.notes.append(f"extraction degraded: {type(exc).__name__}: {exc}")

    return asset
