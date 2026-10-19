"""Independent actual-Claude integration check against local fake providers."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

root = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('adapter', root / 'cc-anthropic-proxy.py')
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)
requests = []
tool_path = ''

class Mock(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        requests.append(body)
        assert self.path == '/v1/chat/completions'
        assert self.headers['Authorization'] == 'Bearer fake-key'
        assert body['model'] == 'gpt-6-astra'
        assert body['stream'] is True
        seen_result = any(m['role'] == 'tool' for m in body['messages'])
        tools = [t['function']['name'] for t in body.get('tools', [])]
        if 'Read' in tools and not seen_result:
            delta = {'tool_calls': [{'index': 0, 'id': 'call_mock_read', 'type': 'function', 'function': {'name': 'Read', 'arguments': json.dumps({'file_path': tool_path})}}]}
            reason = 'tool_calls'
        else:
            delta = {'content': 'MOCK_ADAPTER_OK'}
            reason = 'stop'
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.end_headers()
        for choice in [{'delta': delta, 'finish_reason': None}, {'delta': {}, 'finish_reason': reason}]:
            self.wfile.write(('data: ' + json.dumps({'id': 'mock', 'choices': [dict(index=0, **choice)]}) + '\n\n').encode())
            self.wfile.flush()
        self.wfile.write(b'data: {"choices":[],"usage":{"prompt_tokens":10,"completion_tokens":5}}\n\ndata: [DONE]\n\n')

upstream = ThreadingHTTPServer(('127.0.0.1', 0), Mock)
adapter.UPSTREAMS = {k: 'http://127.0.0.1:%s/v1' % upstream.server_port for k in adapter.UPSTREAMS}
bridge = ThreadingHTTPServer(('127.0.0.1', 0), adapter.Handler)
for server in (upstream, bridge):
    threading.Thread(target=server.serve_forever, daemon=True).start()
try:
    with tempfile.TemporaryDirectory(prefix='cc-critic-') as tmp:
        tool_path = str(Path(tmp) / 'fixture.txt')
        Path(tool_path).write_text('LOCAL_TOOL_RESULT_321', encoding='utf-8')
        env = {k: v for k, v in os.environ.items() if not k.startswith(('ANTHROPIC_', 'CLAUDE_', 'OPENAI_'))}
        env.update(ANTHROPIC_API_KEY='fake-key', CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC='1', CLAUDE_CODE_MAX_CONTEXT_TOKENS='872000')
        for provider in adapter.UPSTREAMS:
            requests.clear()
            env['ANTHROPIC_BASE_URL'] = 'http://127.0.0.1:%s/%s' % (bridge.server_port, provider)
            command = [str(Path.home() / '.local/bin/claude.exe'), '--bare', '--safe-mode', '--setting-sources', '', '--strict-mcp-config', '--no-session-persistence', '--output-format', 'json', '--model', 'gpt-6-astra', '--tools', 'Read', '--allowedTools', 'Read', '--system-prompt', 'Use Read then respond.', '-p', 'Read the fixture then finish.']
            result = subprocess.run(command, cwd=tmp, env=env, capture_output=True, text=True, timeout=55, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            assert result.returncode == 0, (result.returncode, result.stderr[-1500:])
            assert 'MOCK_ADAPTER_OK' in result.stdout, (result.stdout[-1500:], result.stderr[-1500:])
            usage = json.loads(result.stdout)['modelUsage']
            assert usage['gpt-6-astra']['contextWindow'] == 872000, usage
            assert len(requests) >= 2, len(requests)
            final_tools = [m for r in requests for m in r['messages'] if m['role'] == 'tool']
            assert any('LOCAL_TOOL_RESULT_321' in json.dumps(m) for m in final_tools), final_tools
            print('PASS actual Claude:', provider, 'model gpt-6-astra, context window 872000, streamed response, Read tool roundtrip')
finally:
    for server in (bridge, upstream):
        server.shutdown()
        server.server_close()
