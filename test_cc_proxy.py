"""Offline protocol regression check; no credentials or external generation."""
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
from pathlib import Path
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

spec = importlib.util.spec_from_file_location("proxy", Path(__file__).with_name("cc-anthropic-proxy.py"))
proxy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(proxy)

key = proxy.cache_key({"metadata": {"user_id": "user_x_session_1"}})
assert key and key == proxy.cache_key({"metadata": {"user_id": "user_x_session_1"}}), "One stable key per session"
assert key != proxy.cache_key({"metadata": {"user_id": "user_x_session_2"}}) and proxy.cache_key({}) is None

# The CLI route's discovery list comes from cc-switch.py beside the adapter.
with tempfile.TemporaryDirectory() as tmp:
    Path(tmp, "cc-switch.py").write_text("def get_models(provider):\n    return [{'id': provider + '-model'}]\n", encoding="utf-8")
    original_file, proxy.__file__ = proxy.__file__, str(Path(tmp) / "cc-anthropic-proxy.py")
    try:
        assert proxy.cc_models("cli") == [{"id": "cli-model"}]
        proxy._cc_switch = None  # a fresh adapter process
        Path(tmp, "cc-switch.py").unlink()
        assert proxy.cc_models("cli") == [], "Without the sibling script discovery is empty"
    finally:
        proxy.__file__ = original_file
        proxy._cc_switch = None

# A running worker's request takes its profile's current effort, or none when the profile has none.
class _Agents:
    efforts = {"sub": "low", "plain": None}
    def load_agents(self):
        return {name: {"effort": effort} for name, effort in self.efforts.items()}
proxy._cc_switch = _Agents()
try:
    body = {"output_config": {"effort": "xhigh"}, "messages": ["kept"]}
    proxy.live_worker_effort(body, "sub")
    assert body == {"output_config": {"effort": "low"}, "messages": ["kept"]}
    proxy.live_worker_effort(body, "plain")
    assert "effort" not in body["output_config"]
    proxy.live_worker_effort(body := {"output_config": {"effort": "high"}}, "gone")
    assert body["output_config"]["effort"] == "high", "an unknown profile keeps the started effort"
finally:
    proxy._cc_switch = None

body = {"model": "gpt-6-astra", "max_tokens": 100, "stream": True,
    "system": [{"type": "text", "text": "System", "cache_control": {"type": "ephemeral"}}],
    "messages": [{"role": "user", "content": "Read it"},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "call_1", "name": "Read", "input": {"file_path": "test.txt"}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "call_1", "content": "hello"}, {"type": "text", "text": "Explain"}]}],
    "tools": [{"name": "Read", "input_schema": {"type": "object", "properties": {"file_path": {"type": "string"}}}}],
    "tool_choice": {"type": "auto", "disable_parallel_tool_use": True}}
chat = proxy.to_chat(body)
assert [m["role"] for m in chat["messages"]] == ["system", "user", "assistant", "tool", "user"]
assert chat["messages"][3]["content"] == "hello"
assert chat["model"] == "gpt-6-astra" and chat["parallel_tool_calls"] is False
assert chat["tools"][0]["function"]["name"] == "Read"
assert json.loads(chat["messages"][2]["tool_calls"][0]["function"]["arguments"]) == {"file_path": "test.txt"}
# Claude Code's /effort rides output_config; OpenAI tops out at xhigh.
assert "reasoning_effort" not in chat
assert proxy.to_chat({**body, "output_config": {"effort": "low"}})["reasoning_effort"] == "low"
assert proxy.to_chat({**body, "output_config": {"effort": "max"}})["reasoning_effort"] == "xhigh"
assert proxy.content_parts([{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "abc"}}])[0]["image_url"]["url"] == "data:image/png;base64,abc"
try:
    proxy.content_parts([{"type": "document"}])
except ValueError:
    pass
else:
    raise AssertionError("Unsupported content silently discarded")
# Claude Code records dynamically added tools (MCP connectors) as system content blocks.
# They are metadata, not content: they must not fail the request nor reach the model.
dynamic = {**body, "messages": [
    {"role": "system", "content": [{"type": "tool_addition", "tool": {"type": "tool_definition",
        "definition": {"name": "mcp__docs__batch", "input_schema": {"type": "object"}}}}]},
    *body["messages"]]}
assert [m["role"] for m in proxy.to_chat(dynamic)["messages"]] == ["system", "user", "assistant", "tool", "user"]
assert "mcp__docs__batch" not in proxy.to_prompt(dynamic)
# The same bookkeeping can arrive in the top-level system field: no empty system message.
assert "system" not in [m["role"] for m in proxy.to_chat({**body, "system": [{"type": "tool_addition", "tool": {}}]})["messages"]]
assert "<system>" not in proxy.to_prompt({**body, "system": [{"type": "tool_addition", "tool": {}}]})
# And it leaves no blank line in the CLI prompt.
assert "<user>\ngo\n</user>" in proxy.to_prompt({**body, "system": None, "tools": None, "messages": [
    {"role": "user", "content": [{"type": "tool_addition", "tool": {}}, {"type": "text", "text": "go"}]}]})

chunks = [
    {"choices": [{"delta": {"reasoning_content": "Plan: "}}]},
    {"choices": [{"delta": {"reasoning": "read it."}}]},
    {"choices": [{"delta": {"content": "Hello "}}]},
    {"choices": [{"delta": {"content": "world"}}]},
    {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call_2", "function": {"name": "Read", "arguments": '{"file_'}}]}}]},
    {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": 'path":"test.txt"}'}}]}, "finish_reason": "tool_calls"}]},
    {"choices": [], "usage": {"prompt_tokens": 12, "completion_tokens": 8, "prompt_tokens_details": {"cached_tokens": 2}}},
]
wire = b"".join(("data: " + json.dumps(c) + "\n\n").encode() for c in chunks) + b"data: [DONE]\n\n"
events = list(proxy.stream_events(io.BytesIO(wire), body["model"]))
assert events[0]["type"] == "message_start" and events[-1]["type"] == "message_stop"
assert "".join(e["delta"]["text"] for e in events if e.get("delta", {}).get("type") == "text_delta") == "Hello world"
assert next(e for e in events if e.get("delta", {}).get("type") == "input_json_delta")["delta"]["partial_json"] == '{"file_path": "test.txt"}'
assert events[-2]["delta"]["stop_reason"] == "tool_use"
assert events[-2]["usage"]["input_tokens"] == 10
# Reasoning streams as Claude's thinking block, before the answer and tools, each block closed.
starts = [e["content_block"]["type"] for e in events if e["type"] == "content_block_start"]
assert starts == ["thinking", "text", "tool_use"], starts
assert [e["index"] for e in events if e["type"] == "content_block_start"] == [0, 1, 2]
assert [e["index"] for e in events if e["type"] == "content_block_stop"] == [0, 1, 2]
assert "".join(e["delta"]["thinking"] for e in events if e.get("delta", {}).get("type") == "thinking_delta") == "Plan: read it."
assert proxy.to_message({"choices": [{"message": {"reasoning_content": "hm", "content": "ok"}, "finish_reason": "stop"}]}, "m")["content"] == [
    {"type": "thinking", "thinking": "hm", "signature": ""}, {"type": "text", "text": "ok"}]
try:
    list(proxy.stream_events(io.BytesIO(b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'), "test"))
except ValueError:
    pass
else:
    raise AssertionError("Truncated stream reported success")

received = []
# Discovery and effort metadata come from cc-switch; the suites stub that boundary.
proxy.effort_supported = lambda provider, model, effort: True
class Upstream(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path == "/v1/models" and self.headers.get("Authorization") == "Bearer ":
            self.send_response(401)
            self.end_headers()
        elif self.path == "/v1/models":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"data": [{"id": "claude-alpha", "name": "Alpha"}, {"id": "gpt-x"}]}).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        received.append((self.path, self.headers.get("Authorization"), data))
        if data["model"] == "fail" or self.headers.get("Authorization") == "Bearer spent":
            self.send_response(429)
            self.send_header("retry-after", "30")
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream" if data["stream"] else "application/json")
        self.send_header("x-ratelimit-limit-requests", "100")
        self.send_header("x-ratelimit-remaining-requests", "25")
        self.send_header("x-ratelimit-reset-requests", "6m0s")
        self.end_headers()
        self.wfile.write(wire if data["stream"] else json.dumps({"choices": [{"message": {"content": "Ready"}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 1, "completion_tokens": 1}}).encode())

proxy.RATE_DIR = tempfile.mkdtemp()  # never the real status line folder
upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
adapter = ThreadingHTTPServer(("127.0.0.1", 0), proxy.Handler)
for server in (upstream, adapter):
    threading.Thread(target=server.serve_forever, daemon=True).start()
proxy.UPSTREAMS = {provider: f"http://127.0.0.1:{upstream.server_port}/v1" for provider in proxy.UPSTREAMS}
try:
    for provider in proxy.UPSTREAMS:
        url = f"http://127.0.0.1:{adapter.server_port}/{provider}/v1/messages?beta=true"
        for stream in (False, True):
            request = urllib.request.Request(url, data=json.dumps({**body, "stream": stream}).encode(), headers={"Content-Type": "application/json", "x-api-key": "fake-" + provider})
            with urllib.request.urlopen(request, timeout=10) as response:
                result = response.read()
            assert received[-1][0] == "/v1/chat/completions"
            assert received[-1][1] == "Bearer fake-" + provider
            assert received[-1][2]["model"] == body["model"]
            assert b"message_stop" in result if stream else json.loads(result)["content"][0]["text"] == "Ready"
        # Claude Code's /model discovery reads the same route's model list.
        request = urllib.request.Request(f"http://127.0.0.1:{adapter.server_port}/{provider}/v1/models?limit=1000", headers={"x-api-key": "fake"})
        with urllib.request.urlopen(request, timeout=10) as response:
            discovered = json.loads(response.read())
        assert [m["id"] for m in discovered["data"]] == ["claude-alpha", "gpt-x"]
        # A rejected key (or an unreachable upstream) leaves discovery empty, not an error.
        request = urllib.request.Request(f"http://127.0.0.1:{adapter.server_port}/{provider}/v1/models?limit=1000")
        with urllib.request.urlopen(request, timeout=10) as response:
            assert json.loads(response.read())["data"] == []
    # A model with no adjustable effort (MiMo) must not receive reasoning_effort.
    proxy.effort_supported = lambda provider, model, effort: False
    request = urllib.request.Request(url, data=json.dumps({**body, "stream": False, "output_config": {"effort": "high"}}).encode(), headers={"x-api-key": "fake"})
    urllib.request.urlopen(request, timeout=10).read()
    assert "reasoning_effort" not in received[-1][2]
    proxy.effort_supported = lambda provider, model, effort: True
    # `cc official`: passed to Anthropic as sent (path, login, body, stream) until /model routes the launch.
    proxy.OFFICIAL_UPSTREAM = f"http://127.0.0.1:{upstream.server_port}"
    route_for = proxy.route_for
    proxy.route_for = lambda launch: None
    official = f"http://127.0.0.1:{adapter.server_port}/official/v1/messages?beta=true"
    request = urllib.request.Request(official, data=json.dumps({**body, "stream": True}).encode(), headers={"Authorization": "Bearer oauth-login", "x-cc-launch": "L"})
    with urllib.request.urlopen(request, timeout=10) as response:
        assert response.read() == wire, 'The official stream comes back byte for byte'
    assert received[-1] == ("/v1/messages?beta=true", "Bearer oauth-login", {**body, "stream": True})
    # Routed by /model: converted for the picked provider, with its key and model.
    proxy.route_for = lambda launch: {"provider": "nvidia", "model": "routed", "effort": None, "key": "nv-key"}
    request = urllib.request.Request(official, data=json.dumps({**body, "stream": False}).encode(), headers={"Authorization": "Bearer oauth-login", "x-cc-launch": "L"})
    assert json.loads(urllib.request.urlopen(request, timeout=10).read())["content"][0]["text"] == "Ready"
    assert received[-1][:2] == ("/v1/chat/completions", "Bearer nv-key") and received[-1][2]["model"] == "routed"
    proxy.route_for = route_for
    # A key out of quota rests while a spare from <KEY>_API_KEYS serves; the upstream's
    # x-ratelimit-* headers land where the status line reads them.
    proxy.spare_keys = lambda route: ["spare"]
    for _ in range(2):  # the second request skips the resting key outright
        request = urllib.request.Request(url, data=json.dumps({**body, "stream": False}).encode(), headers={"x-api-key": "spent"})
        assert json.loads(urllib.request.urlopen(request, timeout=10).read())["content"][0]["text"] == "Ready"
        assert received[-1][1] == "Bearer spare"
    assert [r[1] for r in received[-3:]] == ["Bearer spent", "Bearer spare", "Bearer spare"]
    saved = json.loads(Path(proxy.RATE_DIR, f"ratelimit-{provider}.json").read_text(encoding="utf-8"))
    window = saved["rate_limits"]["req"]
    assert window["used_percentage"] == 75 and 350 < window["resets_at"] - saved["at"] <= 360
    proxy.spare_keys = lambda route: []
    assert proxy.reset_seconds("1.5s") == 1.5 and proxy.reset_seconds("20ms") == 0.02 and proxy.reset_seconds("bogus") == 60
    # A refused request gets one try on the session's fallback route; other sessions keep the error.
    proxy.fallback_for = lambda launch: {"provider": "nvidia", "model": "backup", "key": "fb"} if launch == "L" else None
    request = urllib.request.Request(url, data=json.dumps({**body, "model": "fail", "stream": False}).encode(), headers={"x-api-key": "fake", "x-cc-launch": "L"})
    assert json.loads(urllib.request.urlopen(request, timeout=10).read())["content"][0]["text"] == "Ready"
    assert received[-1][1] == "Bearer fb" and received[-1][2]["model"] == "backup"
    assert json.loads(Path(proxy.RATE_DIR, "fallback-L.json").read_text(encoding="utf-8"))["provider"] == "nvidia"
    # While the refused key rests, the next request goes straight to the fallback.
    sent = len(received)
    assert json.loads(urllib.request.urlopen(request, timeout=10).read())["content"][0]["text"] == "Ready"
    assert len(received) == sent + 1 and received[-1][1] == "Bearer fb"
    for model, status in [("", 400), ("fail", 429)]:
        request = urllib.request.Request(url, data=json.dumps({**body, "model": model}).encode(), headers={"x-api-key": "fake"})
        try:
            urllib.request.urlopen(request, timeout=10)
        except urllib.error.HTTPError as exc:
            assert exc.code == status
            assert json.load(exc)["type"] == "error"
        else:
            raise AssertionError("Invalid input/upstream error was accepted")
    # A rejected block names itself: the generic adapter message hid this class of failure.
    bad = {**body, "messages": [{"role": "user", "content": [{"type": "document"}]}]}
    request = urllib.request.Request(url, data=json.dumps(bad).encode(), headers={"x-api-key": "fake"})
    try:
        urllib.request.urlopen(request, timeout=10)
    except urllib.error.HTTPError as exc:
        assert exc.code == 400 and "Unsupported content block: document" in json.load(exc)["error"]["message"]
    else:
        raise AssertionError("Unsupported content block was accepted")
finally:
    for server in (adapter, upstream):
        server.shutdown()
        server.server_close()

# Command Code CLI route: a fake cmdc answers with a text tool call.
prompt = proxy.to_prompt(body)
assert '"name": "Read"' in prompt and '<tool_result id="call_1">\nhello\n</tool_result>' in prompt and "<system>\nSystem" in prompt
reply = proxy.from_cli('Checking.\n<tool_call>{"name": "Read", "input": {"file_path": "a.txt"}}</tool_call>', {"inputTokens": 5, "outputTokens": 3}, "end_turn", "m")
assert [b["type"] for b in reply["content"]] == ["text", "tool_use"] and reply["content"][1]["input"] == {"file_path": "a.txt"}
assert reply["stop_reason"] == "tool_use" and reply["usage"]["input_tokens"] == 5
assert proxy.from_cli("<tool_call>not json</tool_call>", None, "end_turn", "m")["content"][0]["type"] == "text"
# A fake cmdc: streams NDJSON events like `cmdc -p --output-format json` and logs its argv.
fake_dir = Path(tempfile.mkdtemp(prefix="fake-cmdc-"))
(fake_dir / "cmdc.py").write_text(r'''import json, sys
from pathlib import Path
Path(__file__).with_name("argv.json").write_text(json.dumps(sys.argv[1:]))
sys.stdin.read()
model = sys.argv[sys.argv.index("--model") + 1]
def event(**e): print(json.dumps({"type": "event", "event": e}), flush=True)
if model == "broke":
    sys.exit(10)
flag = Path(__file__).with_name("flaky.done")
if model == "flaky" and not flag.exists():  # the first run answers nothing (exit 9)
    flag.write_text("")
    event(type="thinking_delta", delta="...")
    sys.exit(9)
if model == "native":  # the model reached for cmdc's own tools: --max-turns 1 exits 8
    event(type="message_end", content=[{"type": "text", "text": "Looking."},
          {"type": "tool_use", "id": "c1", "name": "read_file", "input": {"filePath": "a.txt"}},
          {"type": "tool_use", "id": "c2", "name": "herdr", "input": {}}])
    event(type="turn_end", usage={"inputTokens": 7, "outputTokens": 2})
    sys.exit(8)
event(type="thinking_delta", delta="Hmm")
event(type="text_delta", delta="Checking.\n<tool")
event(type="text_delta", delta='_call>{"name": "Read", "input": {}}</tool_call>')
print(json.dumps({"type": "result", "finalText": 'Checking.\n<tool_call>{"name": "Read", "input": {}}</tool_call>', "usage": {"inputTokens": 1}}))
''', encoding="utf-8")
(fake_dir / "cmdc.cmd").write_text(f'@"{sys.executable}" "%~dp0cmdc.py" %*\n', encoding="utf-8")
proxy.commandcode_executable = lambda: str(fake_dir / "cmdc.cmd")
proxy.cc_models = lambda provider: [{"id": "claude-x"}]  # the plan-filtered list cc-switch owns
argv = lambda: json.loads((fake_dir / "argv.json").read_text())
adapter = ThreadingHTTPServer(("127.0.0.1", 0), proxy.Handler)
threading.Thread(target=adapter.serve_forever, daemon=True).start()
try:
    url = f"http://127.0.0.1:{adapter.server_port}/commandcode-cli/v1/messages"
    for stream in (False, True):
        request = urllib.request.Request(url, data=json.dumps({**body, "model": "deepseek/deepseek-v4-flash", "stream": stream}).encode())
        with urllib.request.urlopen(request, timeout=30) as response:
            result = response.read()
        assert argv()[0] == "-p" and "--max-turns" in argv() and "--effort" not in argv()
        if stream:  # thinking and text arrive live; the <tool_call> text never shows
            events = [json.loads(line[5:]) for line in result.decode().splitlines() if line.startswith("data:")]
            deltas = [e["delta"] for e in events if e["type"] == "content_block_delta"]
            assert [d.get("thinking") for d in deltas if d["type"] == "thinking_delta"] == ["Hmm"]
            assert "".join(d["text"] for d in deltas if d["type"] == "text_delta") == "Checking.\n"
            assert any(d["type"] == "input_json_delta" for d in deltas) and events[-1]["type"] == "message_stop"
        else:
            assert json.loads(result)["content"][1]["name"] == "Read"
    request = urllib.request.Request(url, data=json.dumps({**body, "stream": False, "output_config": {"effort": "high"}}).encode())
    urllib.request.urlopen(request, timeout=30).read()
    assert argv()[-2:] == ["--effort", "high"]
    # The same request against a model that publishes no efforts: cmdc's --effort is omitted.
    proxy.effort_supported = lambda provider, model, effort: False
    request = urllib.request.Request(url, data=json.dumps({**body, "stream": False, "output_config": {"effort": "high"}}).encode())
    urllib.request.urlopen(request, timeout=30).read()
    assert "--effort" not in argv(), 'A model without adjustable effort gets no --effort'
    proxy.effort_supported = lambda provider, model, effort: True
    # cmdc's own read_file becomes the client's Read; an unmatched tool is named, not an error.
    request = urllib.request.Request(url, data=json.dumps({**body, "model": "native", "stream": False}).encode())
    native = json.loads(urllib.request.urlopen(request, timeout=30).read())
    assert native["content"][1] == {**native["content"][1], "name": "Read", "input": {"file_path": "a.txt"}}
    assert "herdr" in native["content"][0]["text"] and native["stop_reason"] == "tool_use"
    assert native["usage"]["input_tokens"] == 7
    # An empty answer (exit 9) is run again instead of reaching Claude as an API error.
    request = urllib.request.Request(url, data=json.dumps({**body, "model": "flaky", "stream": True}).encode())
    flaky = urllib.request.urlopen(request, timeout=30).read()
    assert b"input_json_delta" in flaky and b'"type": "error"' not in flaky and (fake_dir / "flaky.done").exists()
    try:
        urllib.request.urlopen(urllib.request.Request(url, data=json.dumps({**body, "model": "broke", "stream": False}).encode()), timeout=30)
    except urllib.error.HTTPError as exc:
        assert exc.code == 402 and "out of credits" in json.load(exc)["error"]["message"]
    else:
        raise AssertionError("CLI failure was accepted")
    with urllib.request.urlopen(urllib.request.Request(
            f"http://127.0.0.1:{adapter.server_port}/commandcode-cli/v1/models?limit=1000", headers={"x-api-key": "x"}), timeout=10) as response:
        assert json.loads(response.read())["data"] == [{"id": "claude-x", "display_name": "claude-x"}], 'The CLI route offers its plan-filtered ids'
finally:
    adapter.shutdown()
    adapter.server_close()
print("PASS: Messages conversion, streamed text/tools/usage, truncation detection, both provider routes, authentication, HTTP errors; no external requests.")
