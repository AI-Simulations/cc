"""Offline regression check: python -B ~/.config/cc/test_cc_switch.py."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
from unittest.mock import patch

root = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('cc_switch', root / 'cc-switch.py')
cc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cc)

with tempfile.TemporaryDirectory(prefix='cc-check-') as directory:
    temp = Path(directory)
    cc.APPLY_FILE = str(temp / 'apply.ps1')
    cc.STATE_FILE = str(temp / 'state.json')
    cc.OPENCODE_AUTH = str(temp / 'auth.json')
    Path(cc.OPENCODE_AUTH).write_text(json.dumps({'opencode': {'type': 'api', 'key': 'unrelated-key'}}))
    with patch.dict(os.environ, {}, clear=True), patch.object(cc, 'user_env', return_value=''):
        assert cc.auth_key('openrouter') == '', 'Must not leak an unrelated provider key'
        assert cc.auth_key('nvidia') == ''
        assert cc.auth_key('opencode-go') == ''
        assert cc.auth_key('opencode') == 'unrelated-key'
    with patch.dict(os.environ, {'CMD_API_KEY': 'command-test', 'OPENROUTER_API_KEY': 'router-test'}):
        assert cc.auth_key('commandcode') == 'command-test'
        assert cc.auth_key('openrouter') == 'router-test'
    with patch.object(cc, 'fetch', return_value={'data': [
        {'id': 'claude-sonnet-4-6', 'supported_endpoints': ['/messages']},
        {'id': 'openai/gpt-5.3-codex', 'supported_endpoints': ['/responses']},
        {'id': 'gpt-6-sol', 'supported_endpoints': ['/chat/completions', '/responses']},
    ]}), patch.object(cc, 'auth_key', return_value='test'):
        assert [m['id'] for m in cc.get_models('commandcode')] == ['claude-sonnet-4-6', 'gpt-6-sol'], 'Only models Claude Code can reach are listed'
    with patch.object(cc, 'fetch', side_effect=OSError('offline')), contextlib.redirect_stderr(io.StringIO()):
        assert cc.get_models('commandcode')[0]['id'] == 'claude-opus-5-5'

    (temp / 'models_cache.json').write_text(json.dumps({'models': [
        {'slug': 'gpt-6-astra', 'context_window': 272000, 'max_context_window': 872000},
        {'slug': 'gpt-5.5', 'context_window': 272000},
    ]}))
    with patch.dict(os.environ, {'CODEX_HOME': str(temp)}), patch.object(cc, 'ensure_oauth_proxy'), patch.object(cc, 'fetch', return_value={'data': [
        {'id': 'gpt-6-astra'}, {'id': 'gpt-5.5'}, {'id': 'unknown'},
        {'id': 'explicit', 'context_window': 400000},
    ]}):
        assert [m['context'] for m in cc.get_models('openai-oauth')] == [872000, 272000, None, 400000]
        (temp / 'models_cache.json').write_text('invalid json')
        assert [m['context'] for m in cc.get_models('openai-oauth')] == [None, None, None, 400000]
    router_models = [{'id': f'openai/model-{i}', 'context_length': 400000} for i in range(20)]
    with patch.object(cc, 'fetch', return_value={'data': router_models}):
        assert len(cc.get_models('openrouter')) == 20, 'Typed models beyond menu shortlist need limits too'

    fake_key = "test'$(throw 'must not execute')`secret"
    for provider, model in [('commandcode', 'claude-sonnet-4-6'), ('openrouter', 'openai/gpt-5.3-codex'), ('official', None), ('openai-oauth', 'gpt-6-astra'), ('nvidia', 'test/model'), ('gpt4free', 'deepseek-v4-pro'), ('openai-oauth', "model'$(throw 'must not execute')`id")]:
        context = 872000 if provider == 'openai-oauth' and model == 'gpt-6-astra' else 131072 if provider == 'nvidia' else None
        with patch.object(cc, 'get_models', return_value=[{'id': model, 'context': context}]), patch.object(cc, 'auth_key', return_value=fake_key), patch.object(cc, 'ensure_oauth_proxy'), patch.object(cc, 'ensure_g4f'), patch.object(cc, 'ensure_anthropic_proxy'), patch.object(cc.catalog, '_models_dev_facts', return_value={}), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            cc.apply(provider, model)
        script = temp / 'check.ps1'
        script.write_text('''$ErrorActionPreference = 'Stop'
function claude {
    if (Test-Path -LiteralPath $env:CC_TEST_APPLY) { throw 'Apply file must be removed before launch' }
    [pscustomobject]@{
        client = $(if ($script:codexLaunched) { 'codex' } else { 'claude' })
        base = $env:ANTHROPIC_BASE_URL
        key = $env:ANTHROPIC_API_KEY
        token = $env:ANTHROPIC_AUTH_TOKEN
        model = $env:ANTHROPIC_MODEL
        fable = $env:ANTHROPIC_DEFAULT_FABLE_MODEL
        haiku = $env:ANTHROPIC_DEFAULT_HAIKU_MODEL
        sonnet = $env:ANTHROPIC_DEFAULT_SONNET_MODEL
        sonnetName = $env:ANTHROPIC_DEFAULT_SONNET_MODEL_NAME
        connectors = $env:ENABLE_CLAUDEAI_MCP_SERVERS
        apiTimeout = $env:API_TIMEOUT_MS
        subagent = $env:CLAUDE_CODE_SUBAGENT_MODEL
        openai = $env:OPENAI_API_KEY
        openaiBase = $env:OPENAI_BASE_URL
        headers = $env:ANTHROPIC_CUSTOM_HEADERS
        toolSearch = $env:ENABLE_TOOL_SEARCH
        autoModeServer = $env:CLAUDE_CODE_AUTO_MODE_SERVER
        context = $env:CLAUDE_CODE_MAX_CONTEXT_TOKENS
        launchArgs = @($args)
    } | ConvertTo-Json -Compress
}
function codex { $script:codexLaunched = $true; claude @args }
. $env:CC_TEST_APPLY
''', encoding='utf-8')
        env = dict(os.environ, CC_TEST_APPLY=cc.APPLY_FILE)
        env.update({v: 'stale-value' for v in cc.CC_VARS + cc.OAI_VARS})
        result = subprocess.run(['powershell', '-NoProfile', '-File', str(script)], env=env, capture_output=True, text=True, check=True)
        data = json.loads(result.stdout.splitlines()[-1])
        assert not data['headers']
        assert data['toolSearch'] == 'false' if provider in ('openai-oauth', 'nvidia', 'gpt4free') else not data['toolSearch']
        assert data['autoModeServer'] == '0' if provider != 'official' else not data['autoModeServer']
        assert data['connectors'] == 'false' if provider != 'official' else not data['connectors']
        assert data['apiTimeout'] == '1800000' if provider != 'official' else not data['apiTimeout']
        assert data['client'] == 'claude'
        assert data['context'] == str(context) if context else not data['context']
        assert not data['openai'] and not data['openaiBase']
        if provider == 'official':
            assert all(not data[k] for k in ['base', 'key', 'token', 'model', 'fable', 'sonnet', 'subagent'])
            assert data['launchArgs'] == []
        else:
            assert data['base'] == (f'http://127.0.0.1:{cc.ADAPTER_PORT}/{provider}' if provider in ('openai-oauth', 'nvidia', 'gpt4free') else cc.PROVIDERS[provider]['base'])
            assert data['model'] == data['haiku'] == data['subagent'] == model
            # /model offers the provider's other models; Command Code keeps Claude ids on their route.
            picks = cc.picker_models(provider, model)
            assert data['sonnet'] == data['sonnetName'] == (picks[0] if picks else None) and model not in picks
            assert provider != 'commandcode' or all(m.startswith('claude-') for m in picks)
            assert data['launchArgs'] == ['--model', model]
            # A bearer token avoids Claude's claude.ai/ANTHROPIC_API_KEY conflict warning.
            assert data['token'] == ('openai-oauth' if provider == 'openai-oauth' else fake_key) and not data['key']

    for provider, model, key in [('commandcode', '', 'test'), ('commandcode', 'claude-sonnet-4-6', ''), ('openrouter', 'openai/gpt-5.3-codex', '')]:
        with patch.object(cc, 'auth_key', return_value=key), contextlib.redirect_stderr(io.StringIO()):
            try:
                cc.apply(provider, model)
            except SystemExit as exc:
                assert exc.code == 1
            else:
                raise AssertionError('Invalid input accepted')
        assert not Path(cc.APPLY_FILE).exists()
    for provider, model in [('commandcode', 'claude-sonnet-4-6'), ('official', None), ('openai-oauth', 'gpt-6-astra'), ('nvidia', 'test/model')]:
        with patch.object(cc, 'get_models', return_value=[{'id': model, 'context': 872000}]), patch.object(cc, 'auth_key', return_value='test'), patch.object(cc, 'ensure_oauth_proxy'), patch.object(cc, 'ensure_anthropic_proxy'), contextlib.redirect_stdout(io.StringIO()):
            cc.apply(provider, model, no_run=True)
        generated = Path(cc.APPLY_FILE).read_text()
        assert '\nclaude' not in generated and '\ncodex' not in generated
        assert ("Set-Item 'Env:CLAUDE_CODE_MAX_CONTEXT_TOKENS' '872000'" in generated) == (provider != 'official')
        script.write_text("$ErrorActionPreference = 'Stop'\nfunction claude { throw 'Unexpected launch' }\nfunction codex { throw 'Unexpected launch' }\n. $env:CC_TEST_APPLY\n", encoding='utf-8')
        subprocess.run(['powershell', '-NoProfile', '-File', str(script)], env=env, capture_output=True, text=True, check=True)
    for invalid_context in [None, True, '1000000', -1, 0, 1.5]:
        with patch.object(cc, 'get_models', return_value=[{'id': 'test/model', 'context': invalid_context}]), patch.object(cc, 'auth_key', return_value='test'), patch.object(cc, 'ensure_anthropic_proxy'), patch.object(cc.catalog, '_models_dev_facts', return_value={}), contextlib.redirect_stdout(io.StringIO()):
            cc.apply('nvidia', 'test/model', no_run=True)
        assert "Set-Item 'Env:CLAUDE_CODE_MAX_CONTEXT_TOKENS'" not in Path(cc.APPLY_FILE).read_text()
    with patch.object(cc, 'get_models', return_value=[]), patch.object(cc, 'auth_key', return_value='test'), patch.object(cc, 'ensure_anthropic_proxy'), patch.object(cc.catalog, '_models_dev_facts', return_value={}), contextlib.redirect_stdout(io.StringIO()):
        cc.apply('nvidia', 'test/model', effort='xhigh')
        generated = Path(cc.APPLY_FILE).read_text()
        assert "'--effort' 'xhigh'" in generated and "Set-Item 'Env:CLAUDE_CODE_ALWAYS_ENABLE_EFFORT' '1'" in generated
        assert cc.load_state()['effort'] == 'xhigh'
        cc.apply('official')
        assert "Set-Item 'Env:CLAUDE_CODE_ALWAYS_ENABLE_EFFORT'" not in Path(cc.APPLY_FILE).read_text()
print('PASS: context limits and reset, OAuth metadata, credential isolation, model filtering, offline fallback, PowerShell routing/quoting/cleanup, OAuth and NVIDIA Claude launch, official reset, invalid input, NoRun.')

with tempfile.TemporaryDirectory(prefix='cc-entry-check-') as directory:
    result = subprocess.run(['powershell', '-NoProfile', '-File', str(root / 'cc-switch.ps1'), 'invalid-provider'], env=dict(os.environ, USERPROFILE=directory), capture_output=True, text=True)
assert result.returncode != 0, 'Invalid providers must return failure from the PowerShell entry point'
print('PASS: PowerShell entry point propagates errors.')
