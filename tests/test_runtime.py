"""Runtime location (Cloud vs Secure Agent) extraction + graph propagation."""

from cai_docs.graph import build_graph
from cai_docs.models import Asset, Reference


def _proc(name, key, refs=None):
    a = Asset(source_relpath=f"{name}.PROCESS.xml", asset_type="process",
              name=name, guid=key)
    a.references = refs or []
    return a


def test_secure_agent_propagates_process_to_subprocess_to_connection():
    # leaf connection is intrinsically agent-bound
    conn = Asset(source_relpath="AppConnection-JDBC.AI_CONNECTION.xml",
                 asset_type="connection", name="AppConnection-JDBC",
                 guid="K1", runtime="Secure Agent", runtime_detail="TME")
    # subprocess uses the agent-only connection
    sub = _proc("DbSubProcess", "S1",
                [Reference(kind="connection", raw="AppConnection-JDBC",
                           target_name="AppConnection-JDBC")])
    # top process only calls the subprocess (no direct agent dependency)
    top = _proc("TopProcess", "T1",
                [Reference(kind="subprocess", raw="DbSubProcess",
                           target_name="DbSubProcess")])
    # a process that touches nothing agent-bound stays on Cloud
    cloudy = _proc("CloudOnly", "C1")

    g = build_graph([top, sub, conn, cloudy])
    by = {a.name: a for a in g.assets}
    assert by["AppConnection-JDBC"].runtime == "Secure Agent"
    assert by["DbSubProcess"].runtime == "Secure Agent"
    assert by["TopProcess"].runtime == "Secure Agent"  # transitively
    assert "Secure Agent" in (by["TopProcess"].runtime_detail or "")
    assert by["CloudOnly"].runtime == "Cloud"  # default when nothing agent-bound


def test_cloud_service_connector_does_not_force_agent():
    svc = Asset(source_relpath="ServiceConnector-OT.AI_SERVICE_CONNECTOR.xml",
                asset_type="service_connector", name="ServiceConnector-OT",
                guid="V1", runtime="Cloud")
    proc = _proc("RestProcess", "P1",
                 [Reference(kind="service_connector", raw="ServiceConnector-OT",
                            target_name="ServiceConnector-OT")])
    g = build_graph([proc, svc])
    assert {a.name: a.runtime for a in g.assets}["RestProcess"] == "Cloud"
