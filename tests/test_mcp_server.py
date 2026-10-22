import io
import json

from bosshunter.mcp_server import TOOLS, handle, serve


def _fake_http(calls):
    def http(verb, path, body):
        calls.append((verb, path, body))
        if "deliver" in path:
            return 202, {"status": "awaiting_user", "request_id": "ab12"}
        if "bad" in path:
            return 401, {"error": "bad token"}
        return 200, {"ok": True}

    return http


def test_the_handshake_and_the_tool_list(tmp_path):
    http = _fake_http([])
    reply = handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26"}}, http)
    assert reply["result"]["protocolVersion"] == "2025-03-26" and reply["result"]["serverInfo"]["name"] == "bosshunter"
    assert handle({"jsonrpc": "2.0", "method": "notifications/initialized"}, http) is None
    names = {tool["name"] for tool in handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, http)["result"]["tools"]}
    assert names == {tool["name"] for tool in TOOLS} and "bosshunter_deliver" in names
    assert all(tool["inputSchema"]["type"] == "object" for tool in TOOLS)


def test_tool_calls_are_forwarded_to_the_token_protected_routes():
    calls = []
    http = _fake_http(calls)
    reply = handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "bosshunter_events", "arguments": {"since": 7}}}, http)
    assert calls[-1] == ("GET", "/api/agent/control/events?since=7&limit=50", None) and reply["result"]["isError"] is False
    reply = handle({"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "bosshunter_deliver", "arguments": {"platform": "liepin", "limit": 20}}}, http)
    assert calls[-1] == ("POST", "/api/agent/control/deliver", {"platform": "liepin", "limit": 20})
    assert "awaiting_user" in reply["result"]["content"][0]["text"]
    reply = handle({"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "bosshunter_resolve_incident", "arguments": {"incident_id": "bad"}}}, http)
    assert reply["result"]["isError"] is True                                       # the program's refusal reaches the agent


def test_unknown_tools_and_methods_are_errors_and_the_stdio_loop_answers_line_by_line():
    http = _fake_http([])
    assert handle({"jsonrpc": "2.0", "id": 6, "method": "tools/call", "params": {"name": "format_disk"}}, http)["error"]["code"] == -32602
    assert handle({"jsonrpc": "2.0", "id": 7, "method": "nope"}, http)["error"]["code"] == -32601
    out = io.StringIO()
    lines = "\n".join([json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}), "not json", json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"})])
    serve(io.StringIO(lines + "\n"), out, http)
    answers = [json.loads(line) for line in out.getvalue().splitlines()]
    assert answers[0]["result"] == {} and answers[1]["error"]["code"] == -32700 and len(answers) == 2
