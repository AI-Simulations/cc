"""Loopback-only Messages adapter for cc's OpenAI-format providers (stdlib)."""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import queue
import subprocess
import tempfile
import urllib.error
import urllib.parse
import uuid
import os
import re
import sys
import threading
from pathlib import Path

sys.path.insert(0, os.environ.get("AI_SUITE_DIR") or str(Path(__file__).resolve().parents[1] / "ai-suite"))
from ai_suite.service import (AIService, CLI_TIMEOUT, COMMANDCODE_EXIT_MEANINGS, background_process_options,
                              commandcode_executable)

PORT = 10532
UPSTREAMS = {
    "openai-oauth": "http://127.0.0.1:10531/v1",
    "nvidia": "https://integrate.api.nvidia.com/v1",
    "commandcode": "https://api.commandcode.ai/provider/v1",
    "gpt4free": "http://127.0.0.1:1337/v1",
}
# Command Code's Go plan answers only its own CLI, so this route runs `cmdc -p` per request.
CLI_ROUTE = "commandcode-cli"
IDENTITY = {"service": "cc-anthropic-proxy", "version": 9}
TOOL_CALL_RE = re.compile(r"<tool_call>\s*(.*?)\s*</tool_call>", re.S)
TOOL_PROTOCOL = """You are the model behind a client application that runs tools for you.
Your own built-in tools are disabled: never call them, and never claim to have run anything yourself.
Ignore the working directory and environment your own instructions describe; the client's system prompt describes the real ones.
The client's tools, as JSON Schema:
{tools}
To call client tools, end your reply with one or more blocks, each exactly:
<tool_call>{{"name": "<tool name>", "input": {{<arguments>}}}}</tool_call>
Then stop. Results arrive in the next user turn as <tool_result> blocks.{choice}"""


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
    effort = openai_effort(body)
    if effort:
        request["reasoning_effort"] = effort
    return request


def openai_effort(body):
    """Claude Code's /effort (output_config.effort) in OpenAI's spelling; None when unset.
    OpenAI tops out at xhigh, so Claude's max lands there."""
    effort = (body.get("output_config") or {}).get("effort")
    return {"max": "xhigh"}.get(effort, effort) if isinstance(effort, str) and effort else None


def usage(value):
    value = value or {}
    cached = (value.get("prompt_tokens_details") or {}).get("cached_tokens", 0)
    return {"input_tokens": max(0, value.get("prompt_tokens", 0) - cached),
            "output_tokens": value.get("completion_tokens", 0), "cache_read_input_tokens": cached,
            "cache_creation_input_tokens": 0}


def reasoning(message):
    """Chat Completions reasoning text: `reasoning_content` (DeepSeek, NVIDIA, g4f) or
    `reasoning` (OpenRouter-style proxies). Claude Code shows it as a thinking block."""
    value = message.get("reasoning_content") or message.get("reasoning")
    return value if isinstance(value, str) else ""


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
    if reasoning(message):
        content.append({"type": "thinking", "thinking": reasoning(message), "signature": ""})
    if message.get("content"):
        content.append({"type": "text", "text": message["content"]})
    calls = message.get("tool_calls") or []
    content.extend(tool_block(call) for call in calls)
    return {"id": data.get("id") or "msg_" + uuid.uuid4().hex, "type": "message", "role": "assistant",
            "model": model, "content": content, "stop_reason": stop_reason(choice.get("finish_reason"), bool(calls)),
            "stop_sequence": None, "usage": usage(data.get("usage"))}


class Blocks:
    """Streamed content blocks: a new one whenever the kind changes, so Claude Code
    shows thinking live, then the answer, each counted in its token total."""

    def __init__(self):
        self.index, self.kind = -1, None

    def add(self, kind, piece):
        events = []
        if self.kind != kind:
            events += self.close()
            self.index, self.kind = self.index + 1, kind
            events.append({"type": "content_block_start", "index": self.index, "content_block":
                           {"type": "thinking", "thinking": "", "signature": ""} if kind == "thinking" else {"type": "text", "text": ""}})
        events.append({"type": "content_block_delta", "index": self.index, "delta":
                       {"type": "thinking_delta", "thinking": piece} if kind == "thinking" else {"type": "text_delta", "text": piece}})
        return events

    def close(self):
        if not self.kind:
            return []
        self.kind = None
        return [{"type": "content_block_stop", "index": self.index}]

    def tool(self, block):
        self.close()
        self.index += 1
        return [{"type": "content_block_start", "index": self.index, "content_block": {**block, "input": {}}},
                {"type": "content_block_delta", "index": self.index,
                 "delta": {"type": "input_json_delta", "partial_json": json.dumps(block["input"])}},
                {"type": "content_block_stop", "index": self.index}]


def message_start(model):
    return {"type": "message_start", "message": {"id": "msg_" + uuid.uuid4().hex,
        "type": "message", "role": "assistant", "model": model, "content": [],
        "stop_reason": None, "stop_sequence": None, "usage": usage(None)}}


def stream_events(response, model):
    yield message_start(model)
    blocks, calls, tokens, reason = Blocks(), {}, usage(None), None
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
            sent = False
            for kind, piece in (("thinking", reasoning(delta)), ("text", delta.get("content"))):
                if isinstance(piece, str) and piece:
                    yield from blocks.add(kind, piece)
                    sent = True
            # ponytail: buffer tool arguments until complete; stream them if tool latency matters.
            for part in delta.get("tool_calls", []):
                call = calls.setdefault(part["index"], {"id": "", "function": {"name": "", "arguments": ""}})
                if part.get("id"):
                    call["id"] = part["id"]
                for key in ("name", "arguments"):
                    call["function"][key] += (part.get("function") or {}).get(key) or ""
            reason = choice.get("finish_reason") or reason
            if not sent:
                yield {"type": "ping"}
    if reason is None:
        raise ValueError("Upstream stream ended without a finish reason")
    yield from blocks.close()
    for call in calls.values():
        yield from blocks.tool(tool_block(call))
    yield {"type": "message_delta", "delta": {"stop_reason": stop_reason(reason, bool(calls)), "stop_sequence": None}, "usage": tokens}
    yield {"type": "message_stop"}


def cli_text(content):
    return "\n".join(part["text"] if part["type"] == "text" else "[image omitted: this route is text-only]"
                     for part in content_parts(content))


def to_prompt(body):
    """The whole Messages request as one CLI prompt; tools become a text protocol."""
    to_chat(body)  # same validation as the HTTP routes
    sections = []
    if body.get("tools"):
        choice = body.get("tool_choice") or {}
        rule = {"tool": f" You must call {choice.get('name')}.", "any": " You must call a tool.",
                "none": " Do not call any tool this turn."}.get(choice.get("type"), "")
        tools = json.dumps([{k: t.get(k) for k in ("name", "description", "input_schema")} for t in body["tools"]])
        sections.append(TOOL_PROTOCOL.format(tools=tools, choice=rule))
    if body.get("system"):
        sections.append(f"<system>\n{cli_text(body['system'])}\n</system>")
    for message in body["messages"]:
        content = message["content"]
        lines = []
        for block in [{"type": "text", "text": content}] if isinstance(content, str) else content:
            kind = block.get("type")
            if kind == "tool_use":
                lines.append("<tool_call>" + json.dumps({"name": block["name"], "input": block["input"]}) + "</tool_call>")
            elif kind == "tool_result":
                error = ' is_error="true"' if block.get("is_error") else ""
                lines.append(f'<tool_result id="{block["tool_use_id"]}"{error}>\n{cli_text(block.get("content", ""))}\n</tool_result>')
            elif kind not in ("thinking", "redacted_thinking"):
                lines.append(cli_text([block]))
        sections.append(f"<{message['role']}>\n" + "\n".join(lines) + f"\n</{message['role']}>")
    sections.append("Write the next assistant turn only.")
    return "\n\n".join(sections)


def native_calls(content, tools):
    """cmdc's own tool calls as the client's (Claude Code's) tools; unmatched ones by name."""
    names = {tool.get("name") for tool in tools or []}

    def first(args, *keys, empty=False):
        return next((args[k] for k in keys if k in args and (empty or args[k] not in (None, ""))), None)

    calls, skipped = [], []
    for block in content:
        if block.get("type") != "tool_use":
            continue
        name, args = block.get("name"), block.get("input") if isinstance(block.get("input"), dict) else {}
        path = first(args, "file_path", "filePath", "absolute_path", "path", "target_file")
        if name in names:
            mapped = [(name, args)]
        elif name == "shell_command":
            mapped = [("Bash" if "Bash" in names else "PowerShell",
                       {"command": first(args, "command", "cmd"), "description": first(args, "description")})]
        elif name == "read_file":
            mapped = [("Read", {"file_path": path, "offset": first(args, "offset"), "limit": first(args, "limit")})]
        elif name == "read_multiple_files":
            mapped = [("Read", {"file_path": p}) for p in first(args, "paths", "file_paths", "files") or []]
        elif name == "write_file":
            mapped = [("Write", {"file_path": path, "content": first(args, "content", "text", empty=True)})]
        elif name == "edit_file":
            mapped = [("Edit", {"file_path": path, "old_string": first(args, "old_string", "oldString", "old_str", empty=True),
                                "new_string": first(args, "new_string", "newString", "new_str", empty=True),
                                "replace_all": first(args, "replace_all", "replaceAll")})]
        elif name in ("grep", "glob"):
            mapped = [(name.capitalize(), {"pattern": first(args, "pattern", "query", "regex", "glob"),
                                           "path": first(args, "path", "directory", "dir")})]
        elif name == "web_fetch":
            mapped = [("WebFetch", {"url": first(args, "url"), "prompt": first(args, "prompt") or "Return the page's main content."})]
        elif name == "web_search":
            mapped = [("WebSearch", {"query": first(args, "query")})]
        else:
            mapped = []
        if not mapped or any(tool not in names for tool, _ in mapped):
            skipped.append(name)
            continue
        # Missing arguments stay missing: Claude Code reports them to the model as a tool error.
        calls += [{"type": "tool_use", "id": "toolu_" + uuid.uuid4().hex, "name": tool,
                   "input": {k: v for k, v in tool_input.items() if v is not None}} for tool, tool_input in mapped]
    return calls, skipped


def from_cli(text, tokens, stop, model, native=(), tools=()):
    calls, skipped = native_calls(native, tools)
    if skipped:
        text += f"\n\n(Command Code's own tool {', '.join(map(str, skipped))} has no match among this client's tools.)"
    for raw in TOOL_CALL_RE.findall(text):
        try:
            call = json.loads(raw)
        except ValueError:
            continue
        if isinstance(call, dict) and isinstance(call.get("name"), str) and isinstance(call.get("input", {}), dict):
            calls.append({"type": "tool_use", "id": "toolu_" + uuid.uuid4().hex, "name": call["name"], "input": call.get("input", {})})
    text = (TOOL_CALL_RE.sub("", text) if calls else text).strip()
    tokens = tokens or {}
    return {"id": "msg_" + uuid.uuid4().hex, "type": "message", "role": "assistant", "model": model,
            "content": ([{"type": "text", "text": text}] if text else []) + calls,
            "stop_reason": "tool_use" if calls else "max_tokens" if stop in ("max_tokens", "length") else "end_turn",
            "stop_sequence": None, "usage": {"input_tokens": tokens.get("inputTokens", 0),
            "output_tokens": tokens.get("outputTokens", 0), "cache_read_input_tokens": tokens.get("cacheReadTokens", 0),
            "cache_creation_input_tokens": tokens.get("cacheWriteTokens", 0)}}


def cmdc_events(body):
    """cmdc's NDJSON events as they arrive (`--output-format json`); the last item is its
    `result` line. A failed run raises with the CLI's own reason."""
    # --max-turns 1: one model call; if the model reaches for cmdc's own tools, it exits 8.
    args = [commandcode_executable(), "-p", "--output-format", "json", "--model", body["model"], "--max-turns", "1",
            "--skip-onboarding", "--no-skills", "--no-session", "--no-auto-update"]
    if openai_effort(body):
        args += ["--effort", openai_effort(body)]
    flags = background_process_options()
    # Neutral directory, prompt on stdin (Windows caps argv at 32k), as ai_suite's _run_cli.
    proc = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, encoding="utf-8", errors="replace", cwd=tempfile.gettempdir(), **flags)

    def kill():  # the .cmd shim's node child holds the pipes: kill the whole tree
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, **flags)
        proc.kill()

    timer = threading.Timer(CLI_TIMEOUT, kill)
    timer.start()
    errors = []
    threading.Thread(target=lambda: errors.extend(proc.stderr), daemon=True).start()
    try:
        proc.stdin.write(to_prompt(body))
        proc.stdin.close()
    except OSError:
        pass  # it died early; the exit code says why
    result, content, tokens = {}, [], None
    try:
        for line in proc.stdout:
            try:
                item = json.loads(line)
            except ValueError:
                continue
            if isinstance(item, dict) and item.get("type") == "result":
                result = item
            elif isinstance(item, dict) and isinstance(item.get("event"), dict):
                event = item["event"]
                if event.get("type") == "message_end" and isinstance(event.get("content"), list):
                    content = event["content"]
                elif event.get("type") == "turn_end":
                    tokens = event.get("usage")
                yield event
        proc.wait()
    finally:
        timer.cancel()
        if proc.poll() is None:  # the client went away mid-reply
            kill()
    if proc.returncode in (8, 9) and any(block.get("type") == "tool_use" for block in content):
        # The model called cmdc's own tools (which --max-turns 1 stops before running)
        # instead of the text protocol; from_cli hands those calls to the client.
        result = {"type": "result", "usage": tokens, "stopReason": "tool_use", "nativeCalls": content,
                  "finalText": "".join(block.get("text", "") for block in content if block.get("type") == "text")}
    elif proc.returncode or not result:
        # The CLI's own error names the cause, e.g. "You've reached your weekly usage limit. Resets in 16h".
        detail = result.get("error") or COMMANDCODE_EXIT_MEANINGS.get(proc.returncode) or "".join(errors)[-300:].strip() or "no result"
        failure = RuntimeError(f"commandcode exited {proc.returncode}: {detail}"[:400])
        failure.status_code = {3: 401, 4: 403, 5: 429, 10: 402}.get(proc.returncode, 502)
        failure.public = True
        failure.exit_code = proc.returncode
        raise failure
    yield result


def cmdc_retrying(body, tries=3):
    """cmdc_events, run again when the model answered nothing (exit 9, or 8 with no
    usable call): usually transient, and Claude Code's own retry would surface an
    API error first. Never repeats a run whose answer text already went out."""
    for attempt in range(1, tries + 1):
        answered = False
        try:
            for event in cmdc_events(body):
                answered = answered or event.get("type") == "text_delta"
                yield event
            return
        except RuntimeError as exc:
            if attempt == tries or answered or getattr(exc, "exit_code", None) not in (8, 9):
                raise


def commandcode_cli(body):
    *_, result = cmdc_retrying(body)
    return from_cli(result.get("finalText") or "", result.get("usage"), result.get("stopReason"), body["model"],
                       result.get("nativeCalls") or (), body.get("tools"))


def cli_stream(body):
    """cmdc's reply as Claude stream events, live: thinking and text as they arrive.
    Text from the first <tool_call> on is held back and becomes tool_use blocks."""
    yield message_start(body["model"])
    blocks, text, sent, held, result = Blocks(), "", 0, False, {}
    mark = "<tool_call>"
    for event in cmdc_retrying(body):
        kind, delta = event.get("type"), event.get("delta")
        if kind == "result":
            result = event
        elif not isinstance(delta, str) or not delta:
            continue
        elif kind == "thinking_delta":
            yield from blocks.add("thinking", delta)
        elif kind == "text_delta" and not held:
            text += delta
            cut = text.find(mark)
            held = cut >= 0
            if not held:  # keep back a tail that may be the start of a <tool_call>
                cut = len(text) - next((k for k in range(min(len(mark) - 1, len(text)), 0, -1)
                                        if mark.startswith(text[-k:])), 0)
            if cut > sent:
                yield from blocks.add("text", text[sent:cut])
                sent = cut
    message = from_cli(result.get("finalText") or "", result.get("usage"), result.get("stopReason"), body["model"],
                       result.get("nativeCalls") or (), body.get("tools"))
    calls = [block for block in message["content"] if block["type"] == "tool_use"]
    # Whatever the final text adds to what streamed: held-back text that held no
    # parseable call, or the note on an unmatched native tool.
    final = next((block["text"] for block in message["content"] if block["type"] == "text"), "")
    streamed = text[:sent].strip()
    if final.startswith(streamed):
        rest = final[len(streamed):]
        if rest:
            yield from blocks.add("text", rest)
    yield from blocks.close()
    for call in calls:
        yield from blocks.tool(call)
    yield {"type": "message_delta", "delta": {"stop_reason": message["stop_reason"], "stop_sequence": None}, "usage": message["usage"]}
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
            if len(route) != 3 or route[0] not in (*UPSTREAMS, CLI_ROUTE) or route[1:] != ["v1", "messages"]:
                self.send_json(404, {"type": "error", "error": {"type": "not_found_error", "message": "Not found"}})
                return
            if self.headers.get("Origin"):
                self.send_json(403, {"error": "Browser requests are not allowed"})
                return
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 32 * 1024 * 1024:
                raise ValueError("Invalid request size (maximum 32 MiB)")
            body = json.loads(self.rfile.read(length))
            if route[0] == CLI_ROUTE:
                self.answer_cli(body)
                return
            request = to_chat(body)
            key = self.headers.get("x-api-key") or self.headers.get("Authorization", "").removeprefix("Bearer ")
            if not key:
                self.send_json(401, {"type": "error", "error": {"type": "authentication_error", "message": "API key required"}})
                return
            service = AIService(allow_auth_prompt=False, config_overrides={
                "provider": "http", "base_url": UPSTREAMS[route[0]], "api_key": key,
                "use_openai_client": False, "timeout": 240,
                "usage_state_path": "", "groq_rate_state_path": "",
            })
            with service.chat_completion_response(request) as response:
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
            status = exc.code if isinstance(exc, urllib.error.HTTPError) else getattr(exc, "status_code", None) or (400 if isinstance(exc, (ValueError, KeyError, TypeError, AttributeError)) and not streaming else 502)
            error = {"type": "error", "error": {"type": "invalid_request_error" if status == 400 else "api_error",
                "message": str(exc) if getattr(exc, "public", False) else
                f"Adapter request failed ({type(exc).__name__}); check model access and provider availability."}}
            if streaming:
                self.wfile.write(("event: error\ndata: " + json.dumps(error) + "\n\n").encode())
            else:
                self.send_json(status, error)

    def answer_cli(self, body):
        """Runs the CLI and streams its reply as it arrives, with pings while it is quiet."""
        to_prompt(body)  # reject bad input before any response is sent
        if not body.get("stream"):
            self.send_json(200, commandcode_cli(body))
            return
        events, gone = queue.Queue(), threading.Event()

        def run():
            stream = cli_stream(body)
            try:
                for event in stream:
                    if gone.is_set():
                        break
                    events.put(event)
            except Exception as exc:  # reported to the client as a stream error event
                events.put({"type": "error", "error": {"type": "api_error",
                            "message": f"Command Code CLI request failed: {type(exc).__name__}: {exc}"[:400]}})
            finally:
                stream.close()  # a client that left kills the CLI run
                events.put(None)

        threading.Thread(target=run, daemon=True).start()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        try:
            while True:
                try:
                    event = events.get(timeout=5)
                except queue.Empty:
                    event = {"type": "ping"}
                if event is None:
                    break
                self.wfile.write(("event: " + event["type"] + "\ndata: " + json.dumps(event) + "\n\n").encode())
                self.wfile.flush()
        finally:
            gone.set()


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
