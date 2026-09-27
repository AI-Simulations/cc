"""Loopback-only Messages adapter for cc's OpenAI-format providers (stdlib)."""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import urllib.error
import urllib.parse
import urllib.request
import uuid

PORT = 10532
UPSTREAMS = {
    "openai-oauth": "http://127.0.0.1:10531/v1",
    "nvidia": "https://integrate.api.nvidia.com/v1",
}
IDENTITY = {"service": "cc-anthropic-proxy", "version": 1}


def content_parts(content):
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if not isinstance(content, list):
        raise ValueError("Content must be text or a list of blocks")
    result = []
    for part in content:
        kind = part.get("type")
        if kind == "text":
            result.append({"type": "text", "text": part["text"]})
        elif kind == "image":
            source = part["source"]
            url = source["url"] if source["type"] == "url" else (
                f"data:{source['media_type']};base64,{source['data']}")
            result.append({"type": "image_url", "image_url": {"url": url}})
        elif kind in ("thinking", "redacted_thinking"):
            # Provider-specific signatures cannot be replayed to another API.
            continue
        else:
            raise ValueError(f"Unsupported content block: {kind}")
    return result


def to_chat(body):
    if not isinstance(body, dict) or not isinstance(body.get("model"), str) or not body["model"].strip():
        raise ValueError("A nonempty model is required")
    if not isinstance(body.get("messages"), list) or not body["messages"]:
        raise ValueError("messages must be a nonempty list")
    messages = []
    if body.get("system"):
        messages.append({"role": "system", "content": content_parts(body["system"])})
    for message in body["messages"]:
        role = message["role"]
        if role not in ("user", "assistant", "system"):
            raise ValueError("Unsupported message role")
        content = message["content"]
        blocks = [{"type": "text", "text": content}] if isinstance(content, str) else content
        normal, calls, results = [], [], []
        for block in blocks:
            if block.get("type") == "tool_use":
                if role != "assistant":
                    raise ValueError("tool_use requires an assistant message")
                calls.append({"id": block["id"], "type": "function", "function": {
                    "name": block["name"], "arguments": json.dumps(block["input"])}})
            elif block.get("type") == "tool_result":
                if role != "user":
                    raise ValueError("tool_result requires a user message")
                parts = content_parts(block.get("content", ""))
                if block.get("is_error"):
                    parts.insert(0, {"type": "text", "text": "Tool error:"})
                results.append({"role": "tool", "tool_call_id": block["tool_use_id"],
                    "content": "\n".join(part["text"] for part in parts if part["type"] == "text")})
                normal.extend(part for part in parts if part["type"] == "image_url")
            else:
                normal.extend(content_parts([block]))
        messages.extend(results)
        if normal or calls:
            item = {"role": role, "content": normal or None}
            if calls:
                item["tool_calls"] = calls
            messages.append(item)
    request = {"model": body["model"], "messages": messages, "stream": bool(body.get("stream"))}
    if request["stream"]:
        request["stream_options"] = {"include_usage": True}
    for key in ("max_tokens", "temperature", "top_p"):
        if key in body:
            request[key] = body[key]
    if body.get("stop_sequences"):
        request["stop"] = body["stop_sequences"]
    if body.get("tools"):
        request["tools"] = []
        for tool in body["tools"]:
            if "input_schema" not in tool:
                raise ValueError(f"Unsupported server tool type: {tool.get('type')}")
            request["tools"].append({"type": "function", "function": {
                "name": tool["name"], "description": tool.get("description", ""),
                "parameters": tool["input_schema"]}})
    choice = body.get("tool_choice", {})
    if choice.get("type") == "tool":
        request["tool_choice"] = {"type": "function", "function": {"name": choice["name"]}}
    elif choice.get("type") in ("auto", "any", "none"):
        request["tool_choice"] = {"any": "required"}.get(choice["type"], choice["type"])
    if "disable_parallel_tool_use" in choice:
        request["parallel_tool_calls"] = not choice["disable_parallel_tool_use"]
    return request


def usage(value):
    value = value or {}
    cached = (value.get("prompt_tokens_details") or {}).get("cached_tokens", 0)
    return {"input_tokens": max(0, value.get("prompt_tokens", 0) - cached),
            "output_tokens": value.get("completion_tokens", 0), "cache_read_input_tokens": cached,
            "cache_creation_input_tokens": 0}


def tool_block(call):
    arguments = json.loads(call["function"].get("arguments") or "{}")
    if not isinstance(arguments, dict):
        raise ValueError("Upstream tool arguments must be an object")
    return {"type": "tool_use", "id": call["id"], "name": call["function"]["name"], "input": arguments}


def stop_reason(reason, tools=False):
    return "max_tokens" if reason == "length" else "tool_use" if tools else "end_turn"


def to_message(data, model):
    choice = data["choices"][0]
    message = choice["message"]
    content = []
    if message.get("content"):
        content.append({"type": "text", "text": message["content"]})
    calls = message.get("tool_calls") or []
    content.extend(tool_block(call) for call in calls)
    return {"id": data.get("id") or "msg_" + uuid.uuid4().hex, "type": "message", "role": "assistant",
            "model": model, "content": content, "stop_reason": stop_reason(choice.get("finish_reason"), bool(calls)),
            "stop_sequence": None, "usage": usage(data.get("usage"))}


def stream_events(response, model):
    yield {"type": "message_start", "message": {"id": "msg_" + uuid.uuid4().hex,
        "type": "message", "role": "assistant", "model": model, "content": [],
        "stop_reason": None, "stop_sequence": None, "usage": usage(None)}}
    text_open, calls, tokens, reason = False, {}, usage(None), None
    for line in response:
        if line.startswith(b":"):
            yield {"type": "ping"}
        if not line.startswith(b"data:"):
            continue
        payload = line[5:].strip()
        if payload == b"[DONE]":
            break
        data = json.loads(payload)
        if data.get("error"):
            raise ValueError("Upstream returned a stream error")
        if data.get("usage"):
            tokens = usage(data["usage"])
        for choice in data.get("choices", []):
            delta = choice.get("delta") or {}
            if delta.get("content"):
                if not text_open:
                    yield {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}
                    text_open = True
                yield {"type": "content_block_delta", "index": 0,
                       "delta": {"type": "text_delta", "text": delta["content"]}}
            # ponytail: buffer tool arguments until complete; stream them if tool latency matters.
            for part in delta.get("tool_calls", []):
                call = calls.setdefault(part["index"], {"id": "", "function": {"name": "", "arguments": ""}})
                if part.get("id"):
                    call["id"] = part["id"]
                for key in ("name", "arguments"):
                    call["function"][key] += (part.get("function") or {}).get(key) or ""
            reason = choice.get("finish_reason") or reason
            if not delta.get("content"):
                yield {"type": "ping"}
    if reason is None:
        raise ValueError("Upstream stream ended without a finish reason")
    if text_open:
        yield {"type": "content_block_stop", "index": 0}
    for index, call in enumerate(calls.values(), int(text_open)):
        block = tool_block(call)
        arguments = block.pop("input")
        yield {"type": "content_block_start", "index": index, "content_block": {**block, "input": {}}}
        yield {"type": "content_block_delta", "index": index,
               "delta": {"type": "input_json_delta", "partial_json": json.dumps(arguments)}}
        yield {"type": "content_block_stop", "index": index}
    yield {"type": "message_delta", "delta": {"stop_reason": stop_reason(reason, bool(calls)), "stop_sequence": None}, "usage": tokens}
    yield {"type": "message_stop"}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # Never log prompts or credentials.

    def send_json(self, status, body):
        encoded = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self):
        self.send_json(200 if self.path == "/health" else 404, IDENTITY if self.path == "/health" else {"error": "Not found"})

    def do_POST(self):
        streaming = False
        try:
            route = urllib.parse.urlsplit(self.path).path.strip("/").split("/")
            if len(route) != 3 or route[0] not in UPSTREAMS or route[1:] != ["v1", "messages"]:
                self.send_json(404, {"type": "error", "error": {"type": "not_found_error", "message": "Not found"}})
                return
            if self.headers.get("Origin"):
                self.send_json(403, {"error": "Browser requests are not allowed"})
                return
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 32 * 1024 * 1024:
                raise ValueError("Invalid request size (maximum 32 MiB)")
            body = json.loads(self.rfile.read(length))
            request = to_chat(body)
            key = self.headers.get("x-api-key") or self.headers.get("Authorization", "").removeprefix("Bearer ")
            if not key:
                self.send_json(401, {"type": "error", "error": {"type": "authentication_error", "message": "API key required"}})
                return
            headers = {"Content-Type": "application/json", "Authorization": "Bearer " + key}
            upstream = urllib.request.Request(UPSTREAMS[route[0]] + "/chat/completions", data=json.dumps(request).encode(), headers=headers)
            with urllib.request.urlopen(upstream, timeout=240) as response:
                if request["stream"]:
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Cache-Control", "no-cache")
                    self.end_headers()
                    streaming = True
                    for event in stream_events(response, body["model"]):
                        self.wfile.write(("event: " + event["type"] + "\ndata: " + json.dumps(event) + "\n\n").encode())
                        self.wfile.flush()
                else:
                    self.send_json(200, to_message(json.load(response), body["model"]))
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as exc:
            status = exc.code if isinstance(exc, urllib.error.HTTPError) else 400 if isinstance(exc, (ValueError, KeyError, TypeError, AttributeError)) and not streaming else 502
            error = {"type": "error", "error": {"type": "invalid_request_error" if status == 400 else "api_error",
                "message": f"Adapter request failed ({type(exc).__name__}); check model access and provider availability."}}
            if streaming:
                self.wfile.write(("event: error\ndata: " + json.dumps(error) + "\n\n").encode())
            else:
                self.send_json(status, error)


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
