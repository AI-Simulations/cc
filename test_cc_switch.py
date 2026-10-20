"""Offline regression check: python -B ~/.config/cc/test_cc_switch.py."""
import contextlib
import importlib.util
import io
import json
import os
import re
from pathlib import Path
import subprocess
import tempfile
from unittest.mock import patch

root = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('cc_switch', root / 'cc-switch.py')
cc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cc)
# Keep real credentials out of mock failure output, and make runs inside Claude Code match plain runs.
for _name in [k for k in os.environ if k == 'CLAUDECODE' or re.search('KEY|TOKEN|SECRET|PASSWORD', k.upper())]:
    del os.environ[_name]

import ast
for filename in ('test_cc_switch.py', 'test_cc_resume.py', 'critic_mock_check.py'):
    for node in ast.walk(ast.parse((root / filename).read_text(encoding='utf-8'))):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name) and node.func.value.id == 'subprocess'
                and node.func.attr in {'run', 'Popen'}):
            assert any(k.arg == 'creationflags' or k.arg is None for k in node.keywords), f'{filename}:{node.lineno}: test command must be hidden'

with patch.object(cc.subprocess, 'run') as launch:
    launch.return_value.stdout = 'TCP 127.0.0.1:9999 0.0.0.0:0 LISTENING 12345'
    cc.stop_port_owner(9999)
    assert launch.call_count == 2
    assert all(call.kwargs.get('creationflags') == getattr(subprocess, 'CREATE_NO_WINDOW', 0)
               for call in launch.call_args_list), 'background netstat/taskkill must be hidden'

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
    # Command Code lists models the plan (and route) cannot use; the menus keep only the usable ones.
    client_catalog = {'data': [
        {'id': 'allowed/go', 'minimumPlan': 'individual-go', 'category': 'opensource', 'reasoningEfforts': ['low', 'medium']},
        {'id': 'allowed/goat', 'minimumPlan': 'individual-goat', 'category': 'opensource'},
        {'id': 'locked/pro', 'minimumPlan': 'individual-pro', 'category': 'premium'},
        {'id': 'hidden/free', 'minimumPlan': 'individual-go', 'category': 'opensource', 'hidden': True},
        {'id': 'ultra/only', 'minimumPlan': None, 'category': 'premium'},
        {'id': 'premium/noplan', 'category': 'premium'},
        {'id': 'opensource/noplan', 'category': 'opensource'},
        {'id': 'future/opensource', 'minimumPlan': 'individual-mega', 'category': 'opensource'},
        {'id': 'future/premium', 'minimumPlan': 'individual-mega', 'category': 'premium'},
    ]}
    plain_catalog = {'data': [
        {'id': 'allowed/go', 'supported_endpoints': ['/messages']},
        {'id': 'allowed/goat', 'supported_endpoints': ['/chat/completions']},
        {'id': 'locked/pro', 'supported_endpoints': ['/messages']},
    ]}
    def account(plan='individual-goat', status='active', bought=0):
        def fetch(url, headers=None, timeout=15):
            if url.endswith('/subscriptions'):
                return {'data': {'planId': plan, 'status': status}}
            if url.endswith('/credits'):
                return {'credits': {'monthlyCredits': 5, 'purchasedCredits': bought, 'freeCredits': 0}}
            return client_catalog if 'view=client' in url else plain_catalog
        return fetch
    cli_ids = {m['id'] for m in client_catalog['data'] if not m.get('hidden')}
    def listed(provider, **account_args):
        cc._PLAN_CACHE.clear()
        cc._CATALOG_CACHE.clear()
        with patch.object(cc, 'fetch', side_effect=account(**account_args)), patch.object(cc, 'auth_key', return_value='test'), \
             patch.object(cc.catalog, '_live_model_ids', return_value=cli_ids):
            return [m['id'] for m in cc.get_models(provider)]
    assert listed('commandcode') == ['allowed/go', 'allowed/goat'], 'Plan-locked models are hidden'
    # The CLI route applies the same plan; a missing or newer-than-known minimumPlan falls
    # back to the category, and a null one (cmdc's "locked for every plan") is never offered.
    assert listed('commandcode-cli') == ['allowed/go', 'allowed/goat', 'future/opensource', 'opensource/noplan']
    assert listed('commandcode', bought=1) == ['allowed/go', 'allowed/goat', 'locked/pro'], 'Purchased credits unlock every model'
    assert 'hidden/free' not in listed('commandcode-cli', bought=1), 'Hidden models stay hidden'
    assert 'ultra/only' in listed('commandcode-cli', bought=1), 'Credits unlock a null minimumPlan'
    assert listed('commandcode', plan='individual-new') == ['allowed/go', 'allowed/goat', 'locked/pro'], 'An unknown plan keeps every model'
    assert listed('commandcode', status='canceled') == ['allowed/go', 'allowed/goat', 'locked/pro'], 'Only an active subscription enforces its plan'
    # The billing catalog and the route catalogs spell ids differently; matching ignores case.
    def cased(url, headers=None, timeout=15):
        if url.endswith('/subscriptions'):
            return {'data': {'planId': 'individual-goat', 'status': 'active'}}
        if url.endswith('/credits'):
            return {'credits': {}}
        return ({'data': [{'id': 'Allowed/GO', 'minimumPlan': 'individual-go', 'category': 'opensource'},
                          {'id': 'LOCKED/PRO', 'minimumPlan': 'individual-pro', 'category': 'premium'}]}
                if 'view=client' in url else
                {'data': [{'id': 'allowed/go', 'supported_endpoints': ['/messages']},
                          {'id': 'locked/pro', 'supported_endpoints': ['/messages']}]})
    cc._PLAN_CACHE.clear()
    cc._CATALOG_CACHE.clear()
    with patch.object(cc, 'fetch', side_effect=cased), patch.object(cc, 'auth_key', return_value='test'):
        assert [m['id'] for m in cc.get_models('commandcode')] == ['allowed/go'], 'Plan ids match regardless of case'
    # The /model picker offers the live usable models, not only stale fallbacks.
    live = [{'id': 'claude-opus-5-5'}, {'id': 'claude-sonnet-5'}, {'id': 'gpt-6-sol'}]
    with patch.object(cc, 'load_state', return_value={}):
        picks = cc.picker_models('commandcode', 'claude-opus-5-5', live)
    assert picks[0] == 'claude-sonnet-5', 'A live model fills the first /model slot'
    assert 'gpt-6-sol' not in picks, 'The other Command Code route stays out of the picker'
    # Remembered picks that the plan no longer allows (or that are retired) stay out.
    with patch.object(cc, 'load_state', return_value={'recent': {'commandcode-cli': ['google/gemini-4-argon', 'Xiaomi/MIMO-V2.6-PRO']}}):
        picks = cc.picker_models('commandcode-cli', 'xiaomi/mimo-v2.6-pro',
                                 [{'id': 'xiaomi/mimo-v2.6-pro'}, {'id': 'deepseek/deepseek-v4-pro'}])
    assert picks == ['deepseek/deepseek-v4-pro'], 'Stale plan-locked picks stay out of /model'
    # Reasoning-effort support comes from the same catalog (MiMo publishes none).
    cc._CATALOG_CACHE.clear()
    with patch.object(cc, 'fetch', side_effect=account()), patch.object(cc, 'auth_key', return_value='test'):
        assert cc.model_efforts('commandcode-cli', 'allowed/go') == ['low', 'medium']
        assert cc.model_efforts('commandcode-cli', 'allowed/goat') == [], 'No published efforts means none'
        assert cc.model_efforts('commandcode-cli', 'not/listed') is None, 'An unlisted model is unknown'
    def locked_only(url, headers=None, timeout=15):
        if url.endswith('/subscriptions'):
            return {'data': {'planId': 'individual-goat', 'status': 'active'}}
        if url.endswith('/credits'):
            return {'credits': {}}
        return client_catalog if 'view=client' in url else {'data': [{'id': 'locked/pro', 'supported_endpoints': ['/messages']}]}
    cc._PLAN_CACHE.clear()
    with patch.object(cc, 'fetch', side_effect=locked_only), patch.object(cc, 'auth_key', return_value='test'):
        assert cc.get_models('commandcode') == [], 'The offline fallback must not re-offer plan-locked models'

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
        opusCapabilities = $env:ANTHROPIC_DEFAULT_OPUS_MODEL_SUPPORTED_CAPABILITIES
        subagent = $env:CLAUDE_CODE_SUBAGENT_MODEL
        openai = $env:OPENAI_API_KEY
        openaiBase = $env:OPENAI_BASE_URL
        headers = $env:ANTHROPIC_CUSTOM_HEADERS
        launch = $env:CC_LAUNCH_ID
        toolSearch = $env:ENABLE_TOOL_SEARCH
        autoModeServer = $env:CLAUDE_CODE_AUTO_MODE_SERVER
        gateway = $env:CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY
        context = $env:CLAUDE_CODE_MAX_CONTEXT_TOKENS
        launchArgs = @($args)
    } | ConvertTo-Json -Compress
}
function codex { $script:codexLaunched = $true; claude @args }
. $env:CC_TEST_APPLY
''', encoding='utf-8')
        env = dict(os.environ, CC_TEST_APPLY=cc.APPLY_FILE)
        env.update({v: 'stale-value' for v in cc.CC_VARS + cc.OAI_VARS})
        result = subprocess.run(['powershell', '-NoProfile', '-File', str(script)], env=env, capture_output=True, text=True, check=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        data = json.loads(result.stdout.splitlines()[-1])
        assert data['opusCapabilities'] == 'thinking,adaptive_thinking,interleaved_thinking,effort,xhigh_effort,max_effort' if provider == 'commandcode' else not data['opusCapabilities']
        assert data['headers'] == 'x-cc-launch: ' + data['launch'] and len(data['launch']) == 32 if provider in cc.FALLBACK_ROUTES else not data['headers'], 'Adapter routes name their launch for /fallback'
        assert data['toolSearch'] == 'false' if provider in ('openai-oauth', 'nvidia', 'gpt4free') else not data['toolSearch']
        assert data['autoModeServer'] == '0' if provider != 'official' else not data['autoModeServer']
        assert data['gateway'] == '1' if provider not in ('official', 'commandcode') else not data['gateway'], 'The /model picker sees the gateway list'
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
            picks = cc.picker_models(provider, model, [{'id': model, 'context': context}])
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
        subprocess.run(['powershell', '-NoProfile', '-File', str(script)], env=env, capture_output=True, text=True, check=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
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
    result = subprocess.run(['powershell', '-NoProfile', '-File', str(root / 'cc-switch.ps1'), 'invalid-provider'], env=dict(os.environ, USERPROFILE=directory), capture_output=True, text=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
assert result.returncode != 0, 'Invalid providers must return failure from the PowerShell entry point'
print('PASS: PowerShell entry point propagates errors.')

with tempfile.TemporaryDirectory(prefix='cc-agents-check-') as directory:
    temp = Path(directory)
    cc.AGENTS_FILE, cc.AGENTS_DIR, cc.STATE_FILE = str(temp / 'agents.json'), str(temp / 'agents'), str(temp / 'state.json')
    Path(cc.AGENTS_FILE).write_text(json.dumps({
        'cheap': {'provider': 'nvidia', 'model': 'test/model', 'effort': 'low', 'autocompact': 60,
                  'args': ['--permission-mode', 'auto'], 'description': 'Bulk edits.'},
        'plain': {}}))
    # Direct terminal calls may start fresh; in-Claude relays require a context envelope.
    parent = {'ANTHROPIC_BASE_URL': 'https://parent', 'CLAUDE_AUTOCOMPACT_PCT_OVERRIDE': '90', 'CLAUDECODE': '', 'KEEP': 'yes'}
    for agent, autocompact, command in [
            ('cheap', '60', ['claude', '-p', '--strict-mcp-config', '--model', 'test/model', '--effort', 'low', '--permission-mode', 'auto']),
            ('plain', None, ['claude', '-p', '--strict-mcp-config'])]:
        with patch.dict(os.environ, parent), patch.object(cc.subprocess, 'run') as launch, patch.object(cc, 'get_models', return_value=[{'id': 'test/model', 'context': 131072}]), patch.object(cc, 'auth_key', return_value='test'), patch.object(cc, 'ensure_anthropic_proxy'):
            launch.return_value.returncode = 3
            try:
                cc.run_agent(agent, 'täsk')
            except SystemExit as exc:
                assert exc.code == 3, 'The child exit code must reach the relay'
            sent = launch.call_args
        assert sent.args[0] == command and sent.kwargs['input'] == 'täsk'.encode('utf-8')
        child = sent.kwargs['env']
        assert child.get('CLAUDE_AUTOCOMPACT_PCT_OVERRIDE') == autocompact, 'The subagent threshold replaces the parent one'
        assert 'CLAUDECODE' not in child and child['KEEP'] == 'yes'
        assert child.get('ANTHROPIC_BASE_URL') == (f'http://127.0.0.1:{cc.ADAPTER_PORT}/nvidia' if agent == 'cheap' else None)
        assert agent == 'plain' or child['CLAUDE_CODE_MAX_CONTEXT_TOKENS'] == '131072'
    with contextlib.redirect_stdout(io.StringIO()):
        cc.sync_agents()
    shim = (temp / 'agents' / 'cc-cheap.md').read_text(encoding='utf-8')
    # Delegated runs must require the context envelope; direct CLI runs may start fresh.
    assert 'name: cc-cheap' in shim and 'agent-relay --feed cheap <<' in shim and 'nvidia/test/model' not in shim
    assert 'including its leading CC_CONTEXT_V1 line' in shim
    for bad in [{'x': {'provider': 'nope'}}, {'x': {'effort': 'huge'}}, {'x': {'autocompact': 0}}, {'x': {'autocompact': True}}, {'Bad Name': {}}, {'x': {'args': '--x'}}, {'x': {'model': 'bad model'}}, {'x': {'args': ['ok', 1]}}]:
        Path(cc.AGENTS_FILE).write_text(json.dumps(bad))
        with contextlib.redirect_stderr(io.StringIO()):
            try:
                cc.load_agents()
            except SystemExit:
                pass
            else:
                raise AssertionError(f'Invalid subagent profile accepted: {bad}')
    Path(cc.AGENTS_FILE).write_text(json.dumps({'cheap': {'provider': 'nvidia', 'model': 'test/model', 'autocompact': 60}}))
    with patch.object(cc.sys, 'argv', ['cc', 'agents', 'set', 'rev', '--provider', 'official', '--effort', 'MAX', '--autocompact=75']), contextlib.redirect_stdout(io.StringIO()):
        cc.main()
    with patch.object(cc.sys, 'argv', ['cc', 'agents', 'set', 'cheap', '--autocompact', 'default', '--effort', 'low']), contextlib.redirect_stdout(io.StringIO()):
        cc.main()
    assert cc.load_agents() == {'cheap': {'provider': 'nvidia', 'model': 'test/model', 'effort': 'low'},
                                'rev': {'provider': 'official', 'effort': 'max', 'autocompact': 75}}
    with patch.object(cc.sys, 'argv', ['cc', 'agents', 'remove', 'rev']), contextlib.redirect_stdout(io.StringIO()):
        cc.main()
    assert list(cc.load_agents()) == ['cheap'] and not (temp / 'agents' / 'cc-rev.md').exists(), 'Removed profiles lose their relay'
    with patch.object(cc, 'get_models', return_value=[{'id': 'test/model'}, {'id': 'other/model'}]), contextlib.redirect_stdout(io.StringIO()) as models_out:
        cc.agent_models('cheap')
    text = models_out.getvalue()
    assert 'nvidia/test/model' in text and 'effort' in text and '* test/model' in text and 'other/model' in text, 'The model list marks the profile pick'
    with contextlib.redirect_stdout(io.StringIO()) as profiles_out:
        cc.agent_models()
    assert 'cheap:' in profiles_out.getvalue(), 'With no name the profiles are listed'
    with patch.object(cc, 'load_agents', return_value={'cheap': {'provider': 'nvidia', 'model': 'Test/MODEL'}}), \
         patch.object(cc, 'get_models', return_value=[{'id': 'test/model'}]), contextlib.redirect_stdout(io.StringIO()) as cased_out:
        cc.agent_models('cheap')
    assert '* test/model' in cased_out.getvalue(), 'The current-model marker ignores id casing'
    # A model that publishes no reasoning efforts is run without --effort (MiMo rejects it).
    for efforts, expected in (([], ['claude', '-p', '--strict-mcp-config', '--model', 'test/model']),
                              (['low'], ['claude', '-p', '--strict-mcp-config', '--model', 'test/model', '--effort', 'low'])):
        with patch.object(cc, 'route_env', return_value={}), patch.object(cc, 'model_efforts', return_value=efforts), \
             patch.object(cc.subprocess, 'run') as launch, contextlib.redirect_stderr(io.StringIO()):
            launch.return_value.returncode = 0
            with patch.object(cc.sys, 'argv', ['cc', 'agent', 'cheap', 'fix']):
                try:
                    cc.main()
                except SystemExit:
                    pass
            assert launch.call_args.args[0] == expected, f'effort support {efforts}'
    with patch.object(cc.sys, 'argv', ['cc', 'agent', 'cheap', '--provider', 'official', '--autocompact', '40', 'fix', '--model', 'x']), patch.object(cc.subprocess, 'run') as launch:
        launch.return_value.returncode = 0
        try:
            cc.main()
        except SystemExit:
            pass
    assert launch.call_args.args[0] == ['claude', '-p', '--strict-mcp-config', '--effort', 'low'], 'A provider override drops the other provider model'
    assert launch.call_args.kwargs['input'] == b'fix --model x', 'Only leading options are options; the task stays whole'
    assert launch.call_args.kwargs['env']['CLAUDE_AUTOCOMPACT_PCT_OVERRIDE'] == '40'
    assert cc.load_agents()['cheap'].get('autocompact') is None, 'Run options do not change the profile'
    cc.APPLY_FILE = str(temp / 'apply.ps1')
    for argv, expected in [(['cc', '-NoRun', 'official', 'claude-opus-5-5', '--autocompact', '70'], "Set-Item 'Env:CLAUDE_AUTOCOMPACT_PCT_OVERRIDE' '70'"),
                           (['cc', '-NoRun', 'official', 'claude-opus-5-5', '--autocompact=default'], "Remove-Item \"Env:CLAUDE_AUTOCOMPACT_PCT_OVERRIDE\"")]:
        with patch.object(cc.sys, 'argv', argv), contextlib.redirect_stdout(io.StringIO()):
            cc.main()
        assert expected in Path(cc.APPLY_FILE).read_text()
    assert cc.load_state()['autocompact'] == ''
    for bad in ['0', '101', 'x', '5.5']:
        with patch.object(cc.sys, 'argv', ['cc', '-NoRun', 'official', '--autocompact', bad]), contextlib.redirect_stderr(io.StringIO()):
            try:
                cc.main()
            except SystemExit:
                pass
            else:
                raise AssertionError(f'Invalid auto-compact accepted: {bad}')
print('PASS: subagent profiles route, effort, autocompact override, env isolation, relay files, validation, set/remove/run options, main --autocompact.')

# A rejected key from opencode's auth.json is renewed once per process; other keys are never touched.
import opencode_key
cc._OPENCODE_CHECKED.clear()
with patch.object(cc, 'auth_key', return_value='stored'), patch.object(opencode_key, 'stored_key', return_value='stored'), \
        patch.object(opencode_key, 'key_ok', return_value=False), patch.object(opencode_key, 'renew', return_value='stored') as renew, \
        contextlib.redirect_stderr(io.StringIO()):
    cc.ensure_key('opencode-go')
    cc.ensure_key('opencode-go')
    assert renew.call_count == 1
cc._OPENCODE_CHECKED.clear()
with patch.object(cc, 'auth_key', return_value='env-key'), patch.object(opencode_key, 'stored_key', return_value='stored'), \
        patch.object(opencode_key, 'key_ok') as ok, patch.object(opencode_key, 'renew') as renew:
    cc.ensure_key('opencode-go')
    assert not ok.called and not renew.called
assert opencode_key.KEY_RE.search('Copy oc_sk_abc123def456_' + 'x' * 30 + ' Copy')
print('opencode key renewal ok')

# OpenCode Go: /messages models go direct, chat-only ones (MiMo, GLM, ...) through the adapter.
dev = {'opencode-go': {'models': {'mimo-v2.6-pro': {'provider': {'api': 'x'}},
                                  'minimax-m3': {'provider': {'npm': '@ai-sdk/anthropic'}}}}}
with patch.object(cc.catalog, '_models_dev', return_value=dev), patch.object(cc, 'auth_key', return_value='test'), \
        patch.object(cc, 'get_models', return_value=[]), patch.object(cc, 'ensure_anthropic_proxy'), \
        contextlib.redirect_stderr(io.StringIO()):
    assert cc.route_env('opencode-go', 'mimo-v2.6-pro')['ANTHROPIC_BASE_URL'].endswith('/opencode-go')
    direct = cc.route_env('opencode-go', 'minimax-m3')
    assert direct['ANTHROPIC_BASE_URL'] == 'https://opencode.ai/zen/go' and 'x-opencode-session' in direct['ANTHROPIC_CUSTOM_HEADERS']
    assert cc.go_messages('qwen3.9-unlisted') and not cc.go_messages('glm-9-unlisted')
print('opencode-go protocol split ok')
