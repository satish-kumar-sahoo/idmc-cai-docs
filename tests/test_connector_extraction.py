"""Connection + service-connector parameter extraction and secret redaction.

The repo's service-connector XML carries `testWith` attributes containing live
bearer tokens / JWTs / customer PII. Those must never reach the vault, but the
actual parameter/variable definitions must.
"""

import json
from pathlib import Path

from cai_docs.classify import classify
from cai_docs.config import Config
from cai_docs.extract import extract
from cai_docs.models import RawFile
from cai_docs.pipeline import run
from cai_docs.xmlmodel import parse

SCM = Path(__file__).parent / "fixtures" / "scm"
CONN = SCM / "Explore/Proj/Application_Connectors/AppConnection-DASSQLServer.AI_CONNECTION.xml"
SVC = SCM / "Explore/Proj/Service_Connectors/DASConnectorSQLServer.AI_SERVICE_CONNECTOR.xml"


def _asset(p):
    rf = RawFile(relpath=p.name, abs_path=p, ext="xml", data=p.read_bytes())
    doc = parse(rf)
    at, conf, sig = classify(doc)
    return extract(doc, at, conf, sig)


def test_connection_attributes_extracted_and_secrets_redacted():
    a = _asset(CONN)
    assert a.asset_type == "connection"
    cfg = a.config
    assert cfg.get("JDBC Connection URL") == "jdbc:sqlserver://db.example.com:1433"
    assert cfg.get("Username") == "svc_consent"
    # encrypted attribute value must be redacted, never the literal secret
    assert cfg.get("Password") == "<redacted>"
    assert "s3cr3t-should-not-leak" not in json.dumps(cfg)
    # nested consumer params + connector metadata surfaced
    assert cfg.get("consumer:KafkaConsumer.topic") == "consent.changes.dev"
    assert cfg.get("connector.type") == "GenericJDBCAdapter"
    assert cfg.get("connector.plugin") == "ICS"
    assert cfg.get("agent") == "TME_AWS_LZ_DEV"
    # configured against a Secure Agent group -> runs on the Secure Agent
    assert a.runtime == "Secure Agent"
    assert a.runtime_detail == "TME_AWS_LZ_DEV"


def test_service_connector_actions_and_no_testwith_leak():
    a = _asset(SVC)
    assert a.asset_type == "service_connector"
    # connection-level config variables -> inputs
    names = {f.name for f in a.inputs}
    assert {"jdbcUrl", "password"} <= names
    # per-action operations
    by_name = {ac.name: ac for ac in a.connector_actions}
    assert set(by_name) == {"Submit Consent", "SQL DB Query Execution"}
    rest = by_name["Submit Consent"]
    assert rest.kind == "rest" and rest.verb == "POST"
    assert rest.url == "{$OneTrustURL}"
    assert {f.name for f in rest.inputs} == {"Authorization", "JSONPayload"}
    assert any("Content-Type" in h for h in rest.headers)
    # the Authorization header value is templated/redacted, not a real token
    assert not any("eyJSECRET" in h for h in rest.headers)
    sql = by_name["SQL DB Query Execution"]
    assert sql.kind == "sql" and sql.sql == "{$Query}"
    # agentOnly business connector (DAS/JDBC) runs on the Secure Agent
    assert a.runtime == "Secure Agent"
    # the entire serialized asset must not contain any testWith fixture value
    blob = json.dumps(a, default=lambda o: getattr(o, "__dict__", str(o)))
    for leak in (
        "eyJSECRETtokenSHOULDNOTLEAK",
        "do-not-leak-this-password",
        "PII-LEAK-1234",
        "secret-host",
    ):
        assert leak not in blob, f"leaked {leak!r}"


_JWT = (
    "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJyb2xlIjoiQXBpIEtleSBVc2VyIiwic3ViIjoidGVzdCJ9.AbCdEf-1234567890_signature"
)

_PROC_WITH_TOKEN = f"""<aetgt:getResponse
  xmlns:aetgt="http://schemas.active-endpoints.com/appmodules/repository/2010/10/avrepository.xsd"
  xmlns:types1="http://schemas.active-endpoints.com/appmodules/repository/2010/10/avrepository.xsd">
 <types1:Item>
  <types1:Name>TokenProc</types1:Name>
  <types1:MimeType>application/xml+process</types1:MimeType>
  <types1:Entry>
   <process xmlns="http://schemas.active-endpoints.com/appmodules/screenflow/2014/04/avosScreenflow.xsd"
            name="TokenProc">
    <tempFields>
     <field name="t_auth" type="string">
      <options><option name="initialvalue">{_JWT}</option></options>
     </field>
    </tempFields>
    <flow/>
   </process>
  </types1:Entry>
 </types1:Item>
</aetgt:getResponse>"""


def test_hardcoded_token_in_tempfield_is_redacted(tmp_path):
    p = tmp_path / "TokenProc.PROCESS.xml"
    p.write_text(_PROC_WITH_TOKEN, encoding="utf-8")
    a = _asset(p)
    assert a.asset_type == "process"
    tf = next(f for f in a.temp_fields if f.name == "t_auth")
    assert tf.initial_value == "<redacted>"
    blob = json.dumps(a, default=lambda o: getattr(o, "__dict__", str(o)))
    assert _JWT not in blob
    assert "eyJhbGci" not in blob


def test_vault_connector_pages_have_params_and_no_secrets(tmp_path):
    cfg = Config(input_path=SCM, output_dir=tmp_path / "v",
                 cache_dir=tmp_path / "c", use_llm=False)
    run(cfg)
    vault = tmp_path / "v"
    svc_md = next(p for p in vault.rglob("DASConnectorSQLServer*.md")
                  if not p.stem.startswith("_MOC"))
    txt = svc_md.read_text(encoding="utf-8")
    assert "## Actions" in txt
    assert "Submit Consent" in txt and "SQL DB Query Execution" in txt
    assert "```sql" in txt
    conn_md = next(p for p in vault.rglob("AppConnection-DASSQLServer*.md"))
    ctxt = conn_md.read_text(encoding="utf-8")
    assert "## Configuration" in ctxt
    assert "JDBC Connection URL" in ctxt
    # no secret/PII anywhere in the generated vault
    for p in vault.rglob("*.md"):
        body = p.read_text(encoding="utf-8")
        for leak in ("s3cr3t-should-not-leak", "eyJSECRETtokenSHOULDNOTLEAK",
                     "do-not-leak-this-password", "PII-LEAK-1234"):
            assert leak not in body, f"{leak!r} leaked into {p.name}"
