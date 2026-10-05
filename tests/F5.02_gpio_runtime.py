"""FB1/FB2/FB3/FB4/FB5: real runtime origins, passive One Shot and managed-host death."""

import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT), str(ROOT / "tests"), str(Path(__file__).parent)]
from blocs.gpio.block import GpioBlock
from gpio_fixture import Fixture, no_live_process
from block_test_packages import install_test_package, prepare_release_run
from ui_smoke_common import (isolated_server, graph_payload, create_project_api, text_node, data_edge,
    create_run_api, http_json, stop_run_api, wait_for_run_predicate, wait_for_run_terminal)


def main():
    for mode in ("centralized", "zeromq_active"):
        for origin in (None, "managed", "linked"):
            with isolated_server() as server:
                fixture = Fixture(server.root_dir)
                block = GpioBlock()
                node = block.build_node_payload(node_id="gpio-case", position={"x":420,"y":160}, config_overrides=fixture.config())
                if origin:
                    model = install_test_package(server, "gpio", origin=origin)
                    node["block_version"] = model["version"]
                graph = graph_payload("Synthetic button and LED", [text_node("control", "Command", '{"action":"status"}', 60,160), node],
                    [data_edge("commands", "control", 1, "gpio-case", 1)])
                project = create_project_api(server, document=graph)["project"]
                if mode == "centralized":
                    run = create_run_api(server, graph, project_id=project["graph_id"], runtime_mode=mode)
                    final = wait_for_run_terminal(server, run["run_id"], timeout_sec=15)
                    assert final["status"] == "success", final.get("logs")
                    assert json.loads(final["output_values"]["gpio-case:2"]["value"])["code"] == "preview"
                    assert not fixture.calls()
                    print("[ok] GPIO passive One Shot " + str(origin), flush=True)
                    continue
                rid = prepare_release_run(server, project["graph_id"], graph)["run_id"]
                def output(data, port=2):
                    raw = data.get("output_values", {}).get("gpio-case:"+str(port), {}).get("value")
                    return json.loads(raw) if raw else {}
                def wait(predicate):
                    data = wait_for_run_predicate(server, rid, lambda data:data.get("status")=="failed" or predicate(data),
                        "GPIO listener condition missing", timeout_sec=15)
                    assert data.get("status") != "failed", data.get("logs")
                    return data
                def send(obj):
                    http_json(server.base_url, f"/api/runs/{rid}/active/control", method="POST", payload={"action":"publish_output",
                        "node_id":"control", "port_id":1, "value":json.dumps(obj), "content_type":"application/json"})
                try:
                    wait(lambda data:output(data).get("code")=="listening")
                    time.sleep(.1)
                    fixture.set(level=0)
                    pressed = output(wait(lambda data:output(data,1).get("event")=="pressed"),1)
                    assert pressed["offset"] == 17 and pressed["edge"] == "falling"
                    send({"action":"on", "duration_ms":10000, "request_id":"light"})
                    wait(lambda data:output(data).get("request_id")=="light")
                    assert any("WRITE 27 1" in call for call in fixture.calls())
                    send({"action":"pause"})
                    wait(lambda data:output(data).get("code")=="paused")
                    assert "WRITE 27 0" in fixture.calls()[-3]
                    assert "RELEASE" in fixture.calls()[-2]
                    send({"action":"resume"})
                    wait(lambda data:output(data).get("code")=="listening")
                    time.sleep(.1)
                    fixture.set(level=1)
                    released = output(wait(lambda data:output(data,1).get("event")=="released"),1)
                    assert released["event_id"] != pressed["event_id"]
                    send({"action":"write", "offset":3, "value":1})
                    wait(lambda data:output(data).get("code")=="command_rejected")
                    send({"action":"on", "duration_ms":10000, "request_id":"stop-lit"})
                    wait(lambda data:output(data).get("request_id")=="stop-lit")
                finally:
                    stop_run_api(server, rid)
                    final = wait_for_run_terminal(server, rid, timeout_sec=10)
                    assert "shutdown_timeout" not in str(final.get("logs"))
                no_live_process(fixture)
                assert "WRITE 27 0" in fixture.calls()[-3], fixture.calls()
                assert "RELEASE" in fixture.calls()[-2]
                assert not any("BAD_" in line for line in fixture.calls())
                # A subsequent Run while native read blocks must still release OS resources.
                fixture.set(mode="hang")
                rid = prepare_release_run(server, project["graph_id"], graph)["run_id"]
                try:
                    wait(lambda data:output(data).get("code")=="listening")
                finally:
                    stop_run_api(server,rid); wait_for_run_terminal(server,rid,timeout_sec=10)
                no_live_process(fixture)
                print("[ok] GPIO live button, LED rest, pause/Stop and native hang " + str(origin), flush=True)


if __name__ == "__main__":
    main()
