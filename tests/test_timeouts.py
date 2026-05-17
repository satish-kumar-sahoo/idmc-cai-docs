"""Timeout values are logged when configured, and prose that merely mentions
'timeout' (fault messages, HTTP-504 'Gateway Timeout' branches) is ignored."""

from cai_docs.classify import classify
from cai_docs.extract import extract
from cai_docs.models import RawFile
from cai_docs.xmlmodel import parse


def _asset(xml: str, relpath: str):
    rf = RawFile(relpath=relpath, abs_path=None, ext="xml", data=xml.encode())
    doc = parse(rf)
    at, conf, sig = classify(doc)
    return extract(doc, at, conf, sig)


_CONN = """<aetgt:getResponse
 xmlns:aetgt="http://schemas.active-endpoints.com/appmodules/repository/2010/10/avrepository.xsd"
 xmlns:types1="http://schemas.active-endpoints.com/appmodules/repository/2010/10/avrepository.xsd">
 <types1:Item><types1:Name>AppConnection-DB</types1:Name>
  <types1:MimeType>application/xml+connection</types1:MimeType>
  <types1:Entry>
   <connection xmlns="http://schemas.informatica.com/socrates/data-services/2014/04/avosConnections.xsd"
               name="AppConnection-DB">
    <attributes>
     <attribute encrypted="false" name="JDBC Connection URL" value="jdbc:x"/>
     <attribute encrypted="false" name="Connection Timeout" value="30"/>
     <attribute encrypted="false" name="Socket Timeout (ms)" value="60000"/>
    </attributes>
   </connection>
  </types1:Entry>
 </types1:Item></aetgt:getResponse>"""

_SVC = """<aetgt:getResponse
 xmlns:aetgt="http://schemas.active-endpoints.com/appmodules/repository/2010/10/avrepository.xsd"
 xmlns:types1="http://schemas.active-endpoints.com/appmodules/repository/2010/10/avrepository.xsd">
 <types1:Item><types1:Name>SC-OT</types1:Name>
  <types1:MimeType>application/xml+businesssconnector</types1:MimeType>
  <types1:Entry>
   <businessConnector xmlns="http://schemas.informatica.com/socrates/data-services/2014/05/business-connector-model.xsd"
                      name="SC-OT">
    <actions><action name="Call" label="Call">
     <input><parameter name="u" type="url"/></input>
     <binding><restSimpleBinding url="{$u}" verb="POST" timeout="45000"/></binding>
     <output><field name="r" type="string"/></output>
    </action></actions>
   </businessConnector>
  </types1:Entry>
 </types1:Item></aetgt:getResponse>"""

# fault-message prose + HTTP-504 branch: must NOT be picked up as a timeout
_PROC = """<aetgt:getResponse
 xmlns:aetgt="http://schemas.active-endpoints.com/appmodules/repository/2010/10/avrepository.xsd"
 xmlns:types1="http://schemas.active-endpoints.com/appmodules/repository/2010/10/avrepository.xsd">
 <types1:Item><types1:Name>P</types1:Name>
  <types1:MimeType>application/xml+process</types1:MimeType>
  <types1:Entry>
   <process xmlns="http://schemas.active-endpoints.com/appmodules/screenflow/2014/04/avosScreenflow.xsd"
            name="P">
    <flow>
     <throw><throwInput>
       <parameter name="detail" source="constant">DB Connection Timeout</parameter>
       <parameter name="reason" source="constant">DB connection timeout, verify Secure Agent</parameter>
     </throwInput></throw>
     <operation><expression language="XQuery">if($c='504') then 'Gateway Timeout' else 'x'</expression></operation>
     <service><serviceName>X</serviceName><timeout>PT30S</timeout></service>
    </flow>
   </process>
  </types1:Entry>
 </types1:Item></aetgt:getResponse>"""


def test_connection_timeouts_captured():
    a = _asset(_CONN, "AppConnection-DB.AI_CONNECTION.xml")
    assert a.asset_type == "connection"
    joined = " | ".join(a.timeouts)
    assert "Connection Timeout: 30" in joined
    assert "Socket Timeout (ms): 60000" in joined


def test_service_connector_rest_binding_timeout_captured():
    a = _asset(_SVC, "SC-OT.AI_SERVICE_CONNECTOR.xml")
    assert a.asset_type == "service_connector"
    assert any(t.startswith("timeout: 45000") for t in a.timeouts), a.timeouts


def test_process_service_timeout_captured_but_prose_ignored():
    a = _asset(_PROC, "P.PROCESS.xml")
    assert a.asset_type == "process"
    blob = " | ".join(a.timeouts)
    # the configured <timeout> element is logged
    assert "timeout: PT30S" in blob
    # the fault message / HTTP-504 prose is NOT logged
    assert "DB Connection Timeout" not in blob
    assert "Gateway Timeout" not in blob
    assert "verify Secure Agent" not in blob
