"""Offline protocol regression check; no credentials or external generation."""
import importlib.util
import io
import json
from pathlib import Path
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

spec = importlib.util.spec_from_file_location("proxy", Path(__file__).with_name("cc-anthropic-proxy.py"))
proxy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(proxy)

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
assert proxy.content_parts([{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "abc"}}])[0]["image_url"]["url"] == "data:image/png;base64,abc"
try:
    proxy.content_parts([{"type": "document"}])
except ValueError:
    pass
else:
    raise AssertionError("Unsupported content silently discarded")

chunks = [
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
try:
    list(proxy.stream_events(io.BytesIO(b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'), "test"))
except ValueError:
    pass
else:
    raise AssertionError("Truncated stream reported success")

received = []
class Upstream(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        received.append((self.path, self.headers.get("Authorization"), data))
        if data["model"] == "fail":
            self.send_response(429)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream" if data["stream"] else "application/json")
        self.end_headers()
        self.wfile.write(wire if data["stream"] else json.dumps({"choices": [{"message": {"content": "Ready"}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 1, "completion_tokens": 1}}).encode())

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
    for model, status in [("", 400), ("fail", 429)]:
        request = urllib.request.Request(url, data=json.dumps({**body, "model": model}).encode(), headers={"x-api-key": "fake"})
        try:
            urllib.request.urlopen(request, timeout=10)
        except urllib.error.HTTPError as exc:
            assert exc.code == status
            assert json.load(exc)["type"] == "error"
        else:
            raise AssertionError("Invalid input/upstream error was accepted")
finally:
    for server in (adapter, upstream):
        server.shutdown()
        server.server_close()
print("PASS: Messages conversion, streamed text/tools/usage, truncation detection, both provider routes, authentication, HTTP errors; no external requests.")
