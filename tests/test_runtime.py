"""Runtime location: an asset's own runtime is intrinsic and is NOT
overwritten by what it calls. A Cloud process that invokes an agent-bound
connector/subprocess still runs on the Cloud Server; it only *delegates*
those steps to the Secure Agent (surfaced via agent_dependencies)."""

from cai_docs.graph import build_graph
from cai_docs.models import Asset, Reference


def _proc(name, key, refs=None):
    a = Asset(source_relpath=f"{name}.PROCESS.xml", asset_type="process",
              name=name, guid=key)
    a.references = refs or []
    return a


def test_cloud_process_delegates_but_keeps_its_own_runtime():
    conn = Asset(source_relpath="AppConnection-JDBC.AI_CONNECTION.xml",
                 asset_type="connection", name="AppConnection-JDBC",
                 guid="K1", runtime="Secure Agent", runtime_detail="TME")
    sub = _proc("DbSubProcess", "S1",
                [Reference(kind="connection", raw="AppConnection-JDBC",
                           target_name="AppConnection-JDBC")])
    top = _proc("TopProcess", "T1",
                [Reference(kind="subprocess", raw="DbSubProcess",
                           target_name="DbSubProcess")])
    cloudy = _proc("CloudOnly", "C1")

    g = build_graph([top, sub, conn, cloudy])
    by = {a.name: a for a in g.assets}

    # the connection's own runtime is intrinsic
    assert by["AppConnection-JDBC"].runtime == "Secure Agent"
    # the subprocess runs on Cloud but delegates the DB call to the agent
    assert by["DbSubProcess"].runtime == "Cloud"
    assert by["DbSubProcess"].agent_dependencies == ["AppConnection-JDBC"]
    # the top process also stays on Cloud, and sees the delegation
    # transitively (through the Cloud subprocess)
    assert by["TopProcess"].runtime == "Cloud"
    assert by["TopProcess"].agent_dependencies == ["AppConnection-JDBC"]
    # a process touching nothing agent-bound is plain Cloud, no delegation
    assert by["CloudOnly"].runtime == "Cloud"
    assert by["CloudOnly"].agent_dependencies == []


def test_explicitly_pinned_process_stays_secure_agent():
    pinned = _proc("PinnedProc", "P1")
    pinned.runtime = "Secure Agent"  # from a .agent: tag at extraction
    pinned.runtime_detail = "TME_AWS_LZ_DEV"
    caller = _proc("Caller", "C1",
                   [Reference(kind="subprocess", raw="PinnedProc",
                              target_name="PinnedProc")])
    g = build_graph([caller, pinned])
    by = {a.name: a for a in g.assets}
    assert by["PinnedProc"].runtime == "Secure Agent"  # not downgraded
    assert by["PinnedProc"].runtime_detail == "TME_AWS_LZ_DEV"
    # caller runs on Cloud, delegates to the pinned (agent) subprocess
    assert by["Caller"].runtime == "Cloud"
    assert by["Caller"].agent_dependencies == ["PinnedProc"]


def test_cloud_service_connector_creates_no_delegation():
    svc = Asset(source_relpath="ServiceConnector-OT.AI_SERVICE_CONNECTOR.xml",
                asset_type="service_connector", name="ServiceConnector-OT",
                guid="V1", runtime="Cloud")
    proc = _proc("RestProcess", "P1",
                 [Reference(kind="service_connector", raw="ServiceConnector-OT",
                            target_name="ServiceConnector-OT")])
    g = build_graph([proc, svc])
    by = {a.name: a for a in g.assets}
    assert by["RestProcess"].runtime == "Cloud"
    assert by["RestProcess"].agent_dependencies == []
