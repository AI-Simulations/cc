"""Offline CLI and PowerShell resume regression checks."""
import contextlib
import importlib.util
import io
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('cc_resume_switch', ROOT / 'cc-switch.py')
cc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cc)
SESSION = '00000000-0000-4000-8000-000000000001'
# Keep real credentials out of mock failure output, and make runs inside Claude Code match plain runs.
for _name in [k for k in os.environ if k == 'CLAUDECODE' or re.search('KEY|TOKEN|SECRET|PASSWORD', k.upper())]:
    del os.environ[_name]


def runpy_upstreams():
    spec = importlib.util.spec_from_file_location('cc_proxy', ROOT / 'cc-anthropic-proxy.py')
    proxy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(proxy)
    return proxy.UPSTREAMS


def proxy_identity():
    """The adapter's own /health answer, so a version bump cannot leave this suite behind."""
    spec = importlib.util.spec_from_file_location('cc_proxy_identity', ROOT / 'cc-anthropic-proxy.py')
    proxy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(proxy)
    return dict(proxy.IDENTITY)


class ResumeTests(unittest.TestCase):
    def test_adapter_launch_has_no_console(self):
        with patch.object(cc, 'fetch', side_effect=[OSError(), proxy_identity()]), patch.object(cc.subprocess, 'Popen') as launch:
            cc.ensure_anthropic_proxy()
        flags = launch.call_args.kwargs['creationflags']
        self.assertTrue(flags & subprocess.CREATE_NO_WINDOW, 'no console window for the adapter')
        self.assertTrue(flags & getattr(subprocess, 'BELOW_NORMAL_PRIORITY_CLASS', 0), 'the adapter never starves the desktop')

    def test_outdated_adapter_is_replaced_and_other_services_are_left_alone(self):
        current = proxy_identity()
        with patch.object(cc, 'fetch', side_effect=[{**current, 'version': 1}, current]), patch.object(cc, 'stop_port_owner') as stop, patch.object(cc.subprocess, 'Popen') as launch:
            cc.ensure_anthropic_proxy()
        stop.assert_called_once_with(cc.ADAPTER_PORT)
        launch.assert_called_once()
        with patch.object(cc, 'fetch', return_value={'service': 'something-else'}), patch.object(cc, 'stop_port_owner') as stop, patch.object(cc.subprocess, 'Popen') as launch, contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            cc.ensure_anthropic_proxy()
        stop.assert_not_called()
        launch.assert_not_called()

    def test_install_preserves_utf16_profiles(self):
        spec = importlib.util.spec_from_file_location('cc_install', ROOT / 'install-resume.py')
        installer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(installer)
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)/'cc'
            for encoding in ('utf-16-le', 'utf-16-be'):
                with self.subTest(encoding=encoding):
                    profile = Path(tmp)/encoding/'profile.ps1'
                    profile.parent.mkdir()
                    original = ('\ufeff# Existing settings\r\n').encode(encoding)
                    profile.write_bytes(original)
                    with contextlib.redirect_stdout(io.StringIO()):
                        installer.install(target, [profile])
                        first = profile.read_bytes()
                        installer.install(target, [profile])
                    self.assertTrue(first.startswith(original))
                    self.assertIn('function cc {', first.decode(encoding))
                    self.assertEqual(profile.read_bytes(), first)

    def test_install_registers_missing_profiles_and_preserves_existing(self):
        spec = importlib.util.spec_from_file_location('cc_install', ROOT / 'install-resume.py')
        installer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(installer)
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)/'cc'
            existing = Path(tmp)/'WindowsPowerShell'/'profile.ps1'
            missing = Path(tmp)/'PowerShell'/'profile.ps1'
            existing.parent.mkdir()
            original = b"# Preserve existing settings\r\nfunction cc { Write-Output 'existing' }\r\n"
            existing.write_bytes(original)
            commands = Path(tmp)/'commands'
            commands.mkdir()
            with contextlib.redirect_stdout(io.StringIO()):
                installer.install(target, [existing, missing], commands)
                first = missing.read_bytes()
                installer.install(target, [existing, missing], commands)
            self.assertEqual(existing.read_bytes(), original)
            self.assertEqual(missing.read_bytes(), first)
            self.assertIn(b'function cc {', first)
            self.assertIn(b'cc-switch.ps1" @args', first)
            for name in installer.COMMAND_FILES:
                self.assertEqual((commands/name).read_bytes(), (ROOT/'commands'/name).read_bytes(),
                                 f'{name} ships with cc')
            self.assertTrue(list(target.glob('backup-resume-*/commands/fallback.md')),
                            'Reinstalling backs up the shipped commands')
            for name in installer.FILES:
                self.assertEqual((target/name).read_bytes(), (ROOT/name).read_bytes())
            self.assertTrue((target/'cc-anthropic-proxy.py').is_file(),
                            'Fresh installs need the OpenAI-format adapter')
            self.assertTrue((target/'ai_suite/service.py').is_file(),
                            'Fresh installs need the shared AI-suite transport')
            code = "import sys, runpy; sys.path.insert(0, sys.argv[1]); runpy.run_path(sys.argv[1] + '/cc-anthropic-proxy.py'); import ai_suite.service as s; assert s.__file__.startswith(sys.argv[1])"
            subprocess.run([sys.executable, '-I', '-S', '-c', code, str(target)],
                           check=True, capture_output=True, **cc.background_process_options())

    def test_install_clears_claude_alias_that_shadows_provider_launcher(self):
        spec = importlib.util.spec_from_file_location('cc_install', ROOT / 'install-resume.py')
        installer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(installer)
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'cc'
            for encoding in ('utf-8', 'utf-16-le', 'utf-16-be'):
                for existing_function in (False, True):
                    with self.subTest(encoding=encoding, existing_function=existing_function):
                        profile = Path(tmp) / (encoding + str(existing_function)) / 'profile.ps1'
                        profile.parent.mkdir()
                        text = ('function cc { "custom provider menu" }\n' if existing_function else '')
                        text += 'Set-Alias -Name cc -Value claude\nSet-Alias -Name keepme -Value Get-Date\n'
                        original = (('\ufeff' if encoding.startswith('utf-16') else '') + text).encode(encoding)
                        profile.write_bytes(original)
                        with contextlib.redirect_stdout(io.StringIO()):
                            installer.install(target, [profile])
                            first = profile.read_bytes()
                            installer.install(target, [profile])
                        self.assertTrue(first.startswith(original), 'Preserve unrelated profile bytes')
                        self.assertEqual(profile.read_bytes(), first, 'Installation must be idempotent')
                        check = Path(tmp) / 'check.ps1'
                        check.write_text('. $env:CC_TEST_PROFILE\nGet-Command cc | ForEach-Object { $_.CommandType.ToString() }\n(Get-Alias keepme).Definition\n', encoding='utf-8')
                        result = subprocess.run(['powershell', '-NoProfile', '-File', str(check)],
                            env=dict(os.environ, CC_TEST_PROFILE=str(profile)), capture_output=True,
                            text=True, check=True, **cc.background_process_options())
                        self.assertEqual(result.stdout.splitlines(), ['Function', 'Get-Date'])

    def test_resume_cli_always_asks_provider_and_model(self):
        cases = [
            (['--resume', SESSION], ['--resume', SESSION]),
            (['--resume'], ['--resume']),
            (['-r', 'named session'], ['-r', 'named session']),
            (['--continue'], ['--continue']),
            (['-c'], ['-c']),
            ([], []),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            for argv, forwarded in cases:
                with self.subTest(argv=argv), patch.object(cc, 'APPLY_FILE', str(Path(tmp)/'apply.ps1')), patch.object(cc, 'load_state', return_value={'provider': 'nvidia', 'model': 'test/model'}), patch.object(cc, 'apply') as apply, patch.object(cc, 'provider_menu', return_value='official') as menu, patch.object(cc, 'model_menu', return_value='claude-opus-5-5') as models, patch.object(cc, 'effort_menu', return_value='high'), patch.object(cc, 'autocompact_menu', return_value=70), patch.object(cc, 'startup_subagent') as subagents, patch.object(sys, 'argv', ['cc', *argv]):
                    cc.main()
                    menu.assert_called_once()
                    models.assert_called_once_with('official')
                    subagents.assert_called_once()
                    apply.assert_called_once_with('official', 'claude-opus-5-5', False, claude_args=forwarded, effort='high', autocompact=70, fallback=None)

    def test_explicit_provider_and_model_skip_menus(self):
        cases = [
            (['official', 'opus', '--resume', SESSION], 'official', 'opus'),
            (['nvidia', 'other/model', '--resume', SESSION], 'nvidia', 'other/model'),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            for argv, provider, model in cases:
                with self.subTest(argv=argv), patch.object(cc, 'APPLY_FILE', str(Path(tmp)/'apply.ps1')), patch.object(cc, 'apply') as apply, patch.object(cc, 'provider_menu', side_effect=AssertionError('Unexpected menu')), patch.object(cc, 'model_menu', side_effect=AssertionError('Unexpected model menu')), patch.object(cc, 'startup_subagent', side_effect=AssertionError('Unexpected subagent menu')), patch.object(sys, 'argv', ['cc', *argv]):
                    cc.main()
                    apply.assert_called_once_with(provider, model, False, claude_args=['--resume', SESSION], effort=None, autocompact=None, fallback=None)

    def test_provider_only_still_asks_model(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(cc, 'APPLY_FILE', str(Path(tmp)/'apply.ps1')), patch.object(cc, 'provider_menu', side_effect=AssertionError('Unexpected menu')), patch.object(cc, 'model_menu', return_value='test/model') as models, patch.object(cc, 'effort_menu', return_value='') as efforts, patch.object(cc, 'autocompact_menu', return_value=''), patch.object(cc, 'fallback_menu', return_value=None), patch.object(cc, 'startup_subagent'), patch.object(cc, 'apply') as apply, patch.object(sys, 'argv', ['cc', 'nvidia', '--resume', SESSION]):
            cc.main()
            models.assert_called_once_with('nvidia')
            efforts.assert_called_once()
            apply.assert_called_once_with('nvidia', 'test/model', False, claude_args=['--resume', SESSION], effort='', autocompact='', fallback=None)

    def test_launch_env_is_restored_so_plain_claude_stays_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp)/'apply.ps1'
            with patch.object(cc, 'APPLY_FILE', str(script)):
                cc.write_apply({'ANTHROPIC_BASE_URL': 'http://gateway', 'ANTHROPIC_MODEL': 'test/model'}, ['ANTHROPIC_API_KEY'], True, [], 'test/model')
            check = Path(tmp)/'check.ps1'
            check.write_text("$env:ANTHROPIC_API_KEY = 'original'\nfunction claude { \"during=$env:ANTHROPIC_BASE_URL|$env:ANTHROPIC_API_KEY\" }\n. $env:CC_TEST_APPLY\n\"after=$env:ANTHROPIC_BASE_URL|$env:ANTHROPIC_MODEL|$env:ANTHROPIC_API_KEY\"\n", encoding='utf-8')
            env = {k: v for k, v in os.environ.items() if not k.startswith('ANTHROPIC_')}
            result = subprocess.run(['powershell', '-NoProfile', '-File', str(check)], env=dict(env, CC_TEST_APPLY=str(script)), capture_output=True, text=True, check=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            self.assertEqual(result.stdout.split()[-2:], ['during=http://gateway|', 'after=||original'])

    def test_commandcode_non_claude_routes_through_adapter(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(cc, 'APPLY_FILE', str(Path(tmp)/'apply.ps1')), patch.object(cc, 'STATE_FILE', str(Path(tmp)/'state.json')), patch.object(cc, 'auth_key', return_value='test'), patch.object(cc, 'ensure_anthropic_proxy') as proxy, patch.object(cc, 'get_models', return_value=[]), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            cc.apply('commandcode', 'deepseek/deepseek-v4.1-flash', no_run=True)
            text = (Path(tmp)/'apply.ps1').read_text()
        proxy.assert_called_once()
        self.assertIn(f"'http://127.0.0.1:{cc.ADAPTER_PORT}/commandcode'", text)
        self.assertIn('commandcode', runpy_upstreams())

    def test_quoted_arguments_reach_claude(self):
        tricky = "session'$(throw 'must not execute'); with spaces"
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp)/'apply.ps1'
            with patch.object(cc, 'APPLY_FILE', str(script)):
                cc.write_apply({'ANTHROPIC_MODEL': 'test/model'}, [], True, ['--resume', tricky], 'test/model')
            check = Path(tmp)/'check.ps1'
            check.write_text("function claude { ConvertTo-Json -InputObject @($args) -Compress }\n. $env:CC_TEST_APPLY\n", encoding='utf-8')
            result = subprocess.run(['powershell', '-NoProfile', '-File', str(check)], env=dict(os.environ, CC_TEST_APPLY=str(script)), capture_output=True, text=True, check=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            self.assertEqual(json.loads(result.stdout.splitlines()[-1]), ['--model', 'test/model', '--resume', tricky])
            self.assertFalse(script.exists())

    def test_compatibility_entry_forwards_native_flags(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shutil.copyfile(ROOT/'cc-switch.ps1', root/'cc-switch.ps1')
            (root/'cc-switch.py').write_text("import json, sys\nfrom pathlib import Path\nPath(__file__).with_name('args.json').write_text(json.dumps(sys.argv[1:]))\n", encoding='utf-8')
            result = subprocess.run(['powershell', '-NoProfile', '-File', str(root/'cc-switch.ps1'), '--resume', SESSION, '--continue'], capture_output=True, text=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads((root/'args.json').read_text()), ['--resume', SESSION, '--continue'])


class AgentRoutingTests(unittest.TestCase):
    PROFILES = {'sub': {'provider': 'openai-oauth', 'model': 'gpt-test', 'effort': 'xhigh'}}

    def test_install_preserves_settings_and_registers_live_routing_once(self):
        spec = importlib.util.spec_from_file_location('cc_install_routing', ROOT / 'install-resume.py')
        installer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(installer)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            claude = root / 'claude'
            claude.mkdir()
            existing = {'permissions': {'deny': ['Agent(Explore)'], 'defaultMode': 'auto'},
                        'hooks': {'PreToolUse': [{'matcher': 'Bash', 'hooks': [{'type': 'command', 'command': 'rtk hook claude'}]}]},
                        'statusLine': {'type': 'command', 'command': 'old', 'refreshInterval': 60}}
            settings = claude / 'settings.json'
            settings.write_text(json.dumps(existing), encoding='utf-8')
            with contextlib.redirect_stdout(io.StringIO()):
                installer.install(root / 'cc', [], claude=claude)
                first = json.loads(settings.read_text(encoding='utf-8'))
                installer.install(root / 'cc', [], claude=claude)
            self.assertEqual(json.loads(settings.read_text(encoding='utf-8')), first)
            self.assertEqual(first['permissions'], existing['permissions'])
            self.assertEqual(first['hooks']['PreToolUse'][0], existing['hooks']['PreToolUse'][0])
            for event, command in [('PreToolUse', 'hook-agent'), ('SessionStart', 'hook-context'), ('UserPromptSubmit', 'hook-context')]:
                hooks = [h for item in first['hooks'][event] for h in item['hooks'] if command in h.get('command', '')]
                self.assertEqual(len(hooks), 1)
                self.assertNotIn('allow', hooks[0])
            self.assertIn('agent-statusline', first['subagentStatusLine']['command'])
            self.assertEqual(first['statusLine']['refreshInterval'], 60)
            self.assertEqual((claude / 'statusline.py').read_bytes(), (ROOT / 'statusline.py').read_bytes())
            self.assertTrue(list((root / 'cc').glob('backup-resume-*/claude/settings.json')))

    def test_broken_fork_cutoff_never_launches_a_fresh_worker(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            parent = root / (SESSION + '.jsonl')
            parent.write_text('{"type":"user","uuid":"real","message":{"content":"parent"}}\n', encoding='utf-8')
            child = root / SESSION / 'subagents/agent-old.jsonl'
            child.parent.mkdir(parents=True)
            child.write_text(json.dumps({'type': 'fork-context-ref', 'parentSessionId': SESSION,
                                         'parentLastUuid': 'missing', 'contextLength': 1}) + '\n', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'cutoff'):
                with cc._restorable_context(str(child)):
                    self.fail('Unverified history must never reach launch')

    def test_resuming_relay_uses_latest_worker_context_and_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            transcript = root / 'relay.jsonl'
            contexts = root / 'contexts'
            contexts.mkdir()
            entries = []
            for number in (1, 2):
                token = f'00000000-0000-4000-8000-{number:012d}'
                record = {'profile': 'sub', 'session_id': token, 'cwd': str(root)}
                (contexts / (token + '.json')).write_text(json.dumps(record), encoding='utf-8')
                entries.append({'type': 'user', 'message': {'content': cc.CONTEXT_PREFIX + json.dumps({'token': token}) + '\ntask'}})
            transcript.write_text('\n'.join(json.dumps(e) for e in entries) + '\n', encoding='utf-8')
            with patch.object(cc, 'CFG_DIR', str(root)):
                self.assertEqual(cc._recorded_context(transcript)['session_id'], token,
                                 'Resuming the first worker loses work from later continuations')
                (contexts / '00000000-0000-4000-8000-000000000001.json').unlink()
                self.assertEqual(cc._recorded_context(transcript)['session_id'], token,
                                 'An expired older record must not hide the valid latest worker')

    def test_relay_refuses_dropped_context_instead_of_starting_fresh(self):
        with patch.object(cc, 'load_agents', return_value=self.PROFILES), \
             patch.object(cc.subprocess, 'run') as launch, self.assertRaises(SystemExit):
            cc.run_agent('sub', 'task without required context', require_context=True)
        launch.assert_not_called()
        with patch.dict(os.environ, {'CLAUDECODE': '1', 'CC_AGENT_CHILD': ''}), \
             patch.object(cc, 'route_env') as route, \
             patch.object(cc, 'load_agents', return_value=self.PROFILES), \
             patch.object(cc.subprocess, 'run') as launch, self.assertRaises(SystemExit):
            cc.run_agent('sub', 'legacy relay omitted its context')
        route.assert_not_called()
        launch.assert_not_called()

    def test_read_only_worker_stays_read_only_after_relay_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory) / 'agent-old'
            base.with_suffix('.meta.json').write_text('{"agentType":"cc-sub"}', encoding='utf-8')
            base.with_suffix('.jsonl').write_text('{"type":"user","message":{"content":"old"}}\n', encoding='utf-8')
            saved = {'profile': 'sub', 'session_id': SESSION, 'read_only': True}
            payload = {'tool_name': 'Agent', 'permission_mode': 'auto', 'transcript_path': str(base),
                       'tool_input': {'subagent_type': 'cc-sub', 'resume': 'old', 'prompt': 'continue'}}
            with patch.object(cc, 'load_agents', return_value=self.PROFILES), \
                 patch.object(cc, '_agent_files', return_value=base), \
                 patch.object(cc, '_recorded_context', return_value=saved):
                output = cc.agent_hook(payload)['hookSpecificOutput']
            header = output['updatedInput']['prompt'].partition('\n')[0]
            self.assertNotIn('\\', header, 'the relay model collapses JSON backslash escapes')
            context = json.loads(header[len(cc.CONTEXT_PREFIX):])
            self.assertIs(context.get('read_only'), True)

    def test_context_workers_reject_profile_privilege_and_read_only_loader_overrides(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'parent.jsonl'
            source.write_text('{"type":"user","uuid":"p1","message":{"content":"parent"}}\n', encoding='utf-8')
            for flag, read_only in [('--dangerously-skip-permissions', False), ('--allowedTools=Bash', False),
                                    ('--allow-dangerously-skip-permissions', False), ('--permission-prompt-tool', False),
                                    ('--mcp-config=unsafe.json', True), ('--plugin-dir', True), ('--plugin-url', True),
                                    ('--settings', False), ('--agent', True)]:
                with self.subTest(flag=flag):
                    context = {'token': SESSION, 'source': str(source), 'permission_mode': 'default', 'read_only': read_only}
                    profile = {**self.PROFILES['sub'], 'args': [flag]}
                    with patch.object(cc, 'load_agents', return_value={'sub': profile}), \
                         patch.object(cc, 'route_env', return_value={}), patch.object(cc, 'model_efforts', return_value=None), \
                         patch.object(cc.subprocess, 'run') as launch, \
                         contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                        cc.run_agent('sub', cc.CONTEXT_PREFIX + json.dumps(context) + '\ntask')
                    launch.assert_not_called()

    def test_slash_commands_run_without_a_model_turn(self):
        with patch.object(cc, 'load_agents', return_value=self.PROFILES), patch.object(cc.subprocess, 'run') as run:
            run.return_value = Mock(stdout='changed', stderr='')
            out = cc.hook_command({'command_name': 'fallback', 'command_args': 'nvidia a/b'})
            self.assertEqual(out['decision'], 'block')
            self.assertIn('changed', out['reason'])
            self.assertEqual(run.call_args.args[0][-3:], ['fallback', 'nvidia', 'a/b'])
            run.reset_mock()
            # /subagent-autocompact is /model's Compact column now; Claude Code's own commands pass
            for name in ('subagent-autocompact', 'subagent-model', 'review'):
                self.assertEqual(cc.hook_command({'command_name': name, 'command_args': 'sub 70'}), {})
            run.assert_not_called()

    def test_adapter_workers_tag_requests_for_live_effort(self):
        route = {'ANTHROPIC_BASE_URL': f'http://127.0.0.1:{cc.ADAPTER_PORT}/openai-oauth'}
        with patch.object(cc, 'load_agents', return_value=self.PROFILES),              patch.object(cc, 'route_env', return_value=route), patch.object(cc, 'model_efforts', return_value=None),              patch.object(cc.subprocess, 'run') as launch,              patch.dict(os.environ, {'CC_AGENT_CHILD': '1'}), self.assertRaises(SystemExit):
            launch.return_value.returncode = 0
            cc.run_agent('sub', 'task')
        headers = launch.call_args.kwargs['env']['ANTHROPIC_CUSTOM_HEADERS']
        self.assertIn('x-cc-worker-profile: sub', headers, 'the adapter retags live effort per request')
        self.assertIn('x-cc-launch: agent-sub', headers, "the adapter resolves this worker's fallback by launch id")

    def test_removed_profile_does_not_hide_running_worker_snapshot(self):
        payload = {'transcript_path': str(ROOT / 'session.jsonl'), 'tasks': [
            {'id': 'old', 'agentType': 'cc-sub', 'model': 'native-relay', 'status': 'running'}]}
        saved = {**self.PROFILES['sub'], 'profile': 'sub', 'session_id': SESSION}
        with patch.object(cc, 'load_agents', return_value={}), \
             patch.object(cc, '_recorded_context', return_value=saved):
            row = list(cc.agent_statusline(payload))[0]['content']
        self.assertIn('gpt-test', row)
        self.assertNotIn('relay', row)
        self.assertTrue(row.startswith('\x1b[1;97m'), 'a running row is highlighted')

    def hook(self, command, payload, profiles=None, child=False, resume_type=None):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profiles_file = root / 'agents.json'
            profiles_file.write_text(json.dumps(self.PROFILES if profiles is None else profiles), encoding='utf-8')
            pending = root / 'apply.ps1'
            pending.write_text('pending launch', encoding='utf-8')
            payload = {**payload, 'transcript_path': str(root / 'session.jsonl')}
            if resume_type:
                metadata = root / 'session/subagents/agent-old.meta.json'
                metadata.parent.mkdir(parents=True)
                metadata.write_text(json.dumps({'agentType': resume_type}), encoding='utf-8')
                metadata.with_name('agent-old.jsonl').write_text('{"type":"user","message":{"content":"old context"}}\n', encoding='utf-8')
            with patch.object(cc, 'AGENTS_FILE', str(profiles_file)), \
                 patch.object(cc, 'APPLY_FILE', str(pending)), \
                 patch.object(cc.sys, 'argv', ['cc', command]), \
                 patch.object(cc.sys, 'stdin', io.StringIO(json.dumps(payload))), \
                 patch.dict(os.environ, {'CC_AGENT_CHILD': '1' if child else ''}), \
                 patch.object(cc, 'apply', side_effect=AssertionError('Hook must not launch a client')), \
                 contextlib.redirect_stdout(io.StringIO()) as output, contextlib.redirect_stderr(io.StringIO()):
                try:
                    cc.main()
                except SystemExit as exc:
                    self.fail(f'Routing command must handle the hook before launch cleanup: exit {exc.code}')
            self.assertEqual(pending.read_text(encoding='utf-8'), 'pending launch')
            return output.getvalue()

    def test_old_and_new_sessions_block_native_workers_without_silent_fallback(self):
        for kind in ('fork', 'general-purpose', 'Explore', 'Plan', 'custom-agent'):
            with self.subTest(kind=kind):
                payload = {'tool_name': 'Agent', 'permission_mode': 'auto',
                           'tool_input': {'subagent_type': kind, 'prompt': 'task', 'description': 'work'}}
                result = json.loads(self.hook('hook-agent', payload))['hookSpecificOutput']
                self.assertEqual(result['permissionDecision'], 'deny')
                self.assertIn('cc-sub', result['permissionDecisionReason'])
                self.assertNotIn('updatedInput', result, 'Do not silently discard native role/tool restrictions')

    def test_configured_relay_keeps_normal_permissions_and_input(self):
        payload = {'tool_name': 'Agent', 'permission_mode': 'auto', 'tool_input': {
            'subagent_type': 'cc-sub', 'prompt': 'complete task', 'description': 'work',
            'isolation': 'worktree', 'run_in_background': True}}
        result = json.loads(self.hook('hook-agent', payload))['hookSpecificOutput']
        self.assertNotIn('permissionDecision', result, 'Context handoff must not auto-approve the tool')
        updated = result['updatedInput']
        self.assertEqual({k: v for k, v in updated.items() if k != 'prompt'},
                         {k: v for k, v in payload['tool_input'].items() if k != 'prompt'})
        self.assertEqual(updated['prompt'].partition('\n')[2], 'complete task')
        self.assertEqual(json.loads(self.hook('hook-agent', payload, child=True)), {})
        payload['permission_mode'] = 'plan'
        result = json.loads(self.hook('hook-agent', payload))['hookSpecificOutput']
        context = json.loads(result['updatedInput']['prompt'].split('\n', 1)[0][len(cc.CONTEXT_PREFIX):])
        self.assertTrue(context['read_only'])
        self.assertEqual(context['permission_mode'], 'plan')

    def test_old_native_resumes_cannot_masquerade_as_configured_workers(self):
        payload = {'tool_name': 'Agent', 'tool_input': {'subagent_type': 'cc-sub', 'resume': 'old', 'prompt': 'continue'}}
        for prior in ('fork', 'general-purpose'):
            with self.subTest(prior=prior):
                result = json.loads(self.hook('hook-agent', payload, resume_type=prior))['hookSpecificOutput']
                self.assertNotIn('permissionDecision', result)
                self.assertNotIn('resume', result['updatedInput'])
                self.assertIn('agent-old.jsonl', result['updatedInput']['prompt'])
        result = json.loads(self.hook('hook-agent', payload))['hookSpecificOutput']
        self.assertEqual(result['permissionDecision'], 'deny')
        self.assertIn('self-contained', result['permissionDecisionReason'])
        result = json.loads(self.hook('hook-agent', payload, resume_type='cc-sub'))['hookSpecificOutput']
        self.assertEqual(result['updatedInput']['resume'], 'old')

    def test_missing_profiles_block_delegation_and_context_names_current_route(self):
        payload = {'tool_name': 'Task', 'tool_input': {'prompt': 'task'}}
        result = json.loads(self.hook('hook-agent', payload, profiles={}))['hookSpecificOutput']
        self.assertEqual(result['permissionDecision'], 'deny')
        for event in ('SessionStart', 'UserPromptSubmit'):
            result = json.loads(self.hook('hook-context', {'hook_event_name': event}))['hookSpecificOutput']
            self.assertEqual(result['hookEventName'], event)
            self.assertIn('cc-sub', result['additionalContext'])
            self.assertIn('openai-oauth/gpt-test', result['additionalContext'])

    def test_native_rows_are_not_relabelled_after_profiles_change(self):
        payload = {'columns': 160, 'tasks': [
            {'id': 'old', 'agentType': 'fork', 'model': 'claude-opus-5-5', 'description': 'old work', 'status': 'running'},
            {'id': 'cc', 'agentType': 'cc-sub', 'model': 'claude-haiku-test', 'description': 'new work', 'status': 'running'}]}
        rows = [json.loads(line) for line in self.hook('agent-statusline', payload).splitlines()]
        self.assertIn('claude-opus-5-5', rows[0]['content'])
        self.assertNotIn('gpt-test', rows[0]['content'])
        self.assertIn('gpt-test · new work', rows[1]['content'])
        self.assertNotIn('claude-haiku-test', rows[1]['content'], 'the relay model is not the worker')

    def test_worker_marks_child_to_prevent_recursive_routing(self):
        with patch.object(cc, 'load_agents', return_value=self.PROFILES), \
             patch.object(cc, 'route_env', return_value={}), \
             patch.object(cc, 'model_efforts', return_value=None), \
             patch.object(cc.subprocess, 'run') as launch, \
             patch.dict(os.environ, {'CC_AGENT_CHILD': '', 'CC_MAIN_PROVIDER': 'official'}):
            launch.return_value.returncode = 0
            with self.assertRaises(SystemExit):
                cc.run_agent('sub', 'task')
        self.assertEqual(launch.call_args.kwargs['env'].get('CC_AGENT_CHILD'), '1')

    def test_worker_restores_parent_and_old_fork_messages_as_normal_history(self):
        parent_id = '00000000-0000-4000-8000-000000000001'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            parent = root / (parent_id + '.jsonl')
            parent.write_text(json.dumps({'type': 'user', 'uuid': 'p1', 'sessionId': parent_id,
                              'message': {'content': 'PARENT_CONTEXT_SENTINEL'}}) + '\n', encoding='utf-8')
            child = root / parent_id / 'subagents/agent-old.jsonl'
            child.parent.mkdir(parents=True)
            entries = [{'type': 'fork-context-ref', 'parentSessionId': parent_id, 'parentLastUuid': 'p1', 'contextLength': 1},
                       {'type': 'user', 'uuid': 'c1', 'parentUuid': None, 'isSidechain': True, 'agentId': 'old',
                        'sessionId': parent_id, 'message': {'content': 'OLD_FORK_ONLY_SENTINEL'}}]
            child.write_text('\n'.join(json.dumps(e) for e in entries) + '\n', encoding='utf-8')
            original_parent, original_child = parent.read_bytes(), child.read_bytes()
            context = {'token': '00000000-0000-4000-8000-000000000002', 'source': str(child), 'permission_mode': 'plan', 'read_only': True}

            def launched(command, **kwargs):
                restored = Path(command[command.index('--resume') + 1])
                records = [json.loads(line) for line in restored.read_text(encoding='utf-8').splitlines()]
                text = json.dumps(records)
                self.assertIn('PARENT_CONTEXT_SENTINEL', text)
                self.assertIn('OLD_FORK_ONLY_SENTINEL', text)
                self.assertNotIn('fork-context-ref', text)
                old = next(e for e in records if e.get('uuid') == 'c1')
                self.assertIs(old.get('isSidechain'), False, 'Claude silently drops sidechain history otherwise')
                self.assertEqual(old['parentUuid'], 'p1')
                self.assertNotIn('agentId', old)
                self.assertIn('--fork-session', command)
                efforts = [command[i + 1] for i, value in enumerate(command) if value == '--effort']
                self.assertEqual(efforts[-1], 'xhigh', 'Profile args must not override the configured effort')
                self.assertEqual(command[-2:], ['--permission-mode', 'plan'])
                self.assertNotIn('CLAUDECODE', kwargs['env'])
                saved = json.loads((root / 'cc/contexts' / (context['token'] + '.json')).read_text(encoding='utf-8'))
                self.assertIs(saved.get('read_only'), True)
                return Mock(returncode=0)

            with patch.object(cc, 'CFG_DIR', str(root / 'cc')), \
                 patch.dict(os.environ, {'CLAUDECODE': '1', 'CC_AGENT_CHILD': ''}), \
                 patch.object(cc, 'load_agents', return_value={'sub': {**self.PROFILES['sub'], 'args': ['--effort', 'low']}}), \
                 patch.object(cc, 'route_env', return_value={}), \
                 patch.object(cc, 'model_efforts', return_value=None), \
                 patch.object(cc.subprocess, 'run', side_effect=launched), self.assertRaises(SystemExit):
                cc.run_agent('sub', cc.CONTEXT_PREFIX + json.dumps(context) + '\nContinue the task')
            self.assertEqual(parent.read_bytes(), original_parent)
            self.assertEqual(child.read_bytes(), original_child)


class NavigationTests(unittest.TestCase):
    PROMPTS = ['Provider', 'Model', 'Effort', 'Auto-compact', 'Subagent provider',
               'Subagent model', 'Subagent effort', 'Subagent auto-compact']

    def run_keys(self, keys, argv=(), agents=None):
        """Exercise the real menus and launcher with only keyboard and external work stubbed."""
        seen = []
        stream = iter(keys)
        screen = io.StringIO()
        screen.isatty = lambda: True
        arrow_menu = cc.catalog._arrow_menu

        def getch():
            try:
                return next(stream)
            except StopIteration:
                self.fail(f'Unexpected extra key read at {seen[-1][0]}; Escape did not go back')

        def menu(title, rows, default, *args, **kwargs):
            seen.append((kwargs.get('name') or title, default))
            save.assert_not_called()  # Backtracking must not save an unfinished profile.
            return arrow_menu(title, rows, default, *args, **kwargs)

        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(cc, 'APPLY_FILE', str(Path(tmp) / 'apply.ps1')), \
             patch.object(cc, 'load_state', return_value={}), \
             patch.object(cc, 'load_agents', return_value=agents or {}), \
             patch.object(cc, '_latest_session_agents', return_value=None), \
             patch.object(cc, 'save_agents') as save, patch.object(cc, 'sync_agents'), \
             patch.object(cc, 'quit_on_ctrl_c'), patch.object(cc, 'apply') as apply, \
             patch.object(cc, 'get_models', side_effect=lambda p: [{'id': p + '/test'}]), \
             patch.object(cc.catalog, '_loading', return_value=contextlib.nullcontext()), \
             patch.object(cc.catalog, '_models_dev', return_value={}), \
             patch.object(cc.catalog, '_artificial_analysis', return_value=[]), \
             patch.object(cc.catalog, '_model_facts', return_value={}), \
             patch.object(cc.catalog, '_refresh_missing_intelligence'), \
             patch.object(cc.catalog, '_enable_ansi'), \
             patch.object(cc.catalog, '_arrow_menu', side_effect=menu), \
             patch.dict(sys.modules, {'msvcrt': Mock(getwch=getch)}), \
             patch.object(sys, 'stdin', Mock(isatty=lambda: True)), \
             patch.object(sys, 'stdout', screen), patch.object(sys, 'argv', ['cc', *argv]):
            cc.main()
        return seen, apply, save, screen.getvalue()

    def test_escape_returns_to_each_previous_main_and_subagent_prompt(self):
        for at in range(1, len(self.PROMPTS)):
            with self.subTest(prompt=self.PROMPTS[at]):
                seen, apply, save, _ = self.run_keys('\r' * at + '\x1b' + '\r' * (9 - at))
                self.assertEqual([name for name, _ in seen],
                                 self.PROMPTS[:at + 1] + self.PROMPTS[at - 1:])
                apply.assert_called_once_with('official', 'official/test', False,
                                              claude_args=[], effort='', autocompact='', fallback=None)
                save.assert_called_once()

    def test_escape_at_first_prompt_and_ctrl_c_cancel_without_saving(self):
        cases = ['\x1b'] + ['\r' * at + '\x03' for at in range(len(self.PROMPTS))]
        for keys in cases:
            with self.subTest(keys=repr(keys)):
                _, apply, save, output = self.run_keys(keys)
                apply.assert_not_called()
                save.assert_not_called()
                self.assertIn('No change.', output)

    def test_backtracking_keeps_answers_and_changing_provider_drops_old_model(self):
        seen, apply, save, _ = self.run_keys('\r\rhigh\r\x1b\r80\r\x1b\rskip\r')
        self.assertEqual([default for name, default in seen if name == 'Effort'], ['default', 'high'])
        self.assertEqual([default for name, default in seen if name == 'Auto-compact'],
                         ['default', 'default', '80'])
        # A direct route has nothing to fall back to, so it is not asked; the question's default is no.
        self.assertNotIn('Add a fallback model?', [name for name, _ in seen])
        apply.assert_called_once_with('official', 'official/test', False,
                                      claude_args=[], effort='high', autocompact=80, fallback=None)
        save.assert_not_called()
        seen, apply, _, _ = self.run_keys('\r\r\x1b\x1bnvidia\r\r\r\r\rskip\r')
        self.assertEqual([default for name, default in seen if name == 'Model'],
                         ['official/test', 'official/test', 'nvidia/test'])
        self.assertEqual(apply.call_args.args[:2], ('nvidia', 'nvidia/test'))
        # An adapter route asks the same question; Escape there returns to auto-compact.
        self.assertEqual([default for name, default in seen if name == 'Add a fallback model?'], ['no'])
        self.assertIsNone(apply.call_args.kwargs['fallback'])
        seen, apply, _, _ = self.run_keys('nvidia\r\r\r\ryes\r\x1b\r\rskip\r')
        at = [name for name, _ in seen].index('Fallback provider')
        self.assertEqual([name for name, _ in seen][at:at + 2], ['Fallback provider', 'Auto-compact'])
        self.assertIsNone(apply.call_args.kwargs['fallback'])
        seen, apply, _, _ = self.run_keys('nvidia\r\r\r\ryes\rgemini\r\rskip\r')
        self.assertEqual(apply.call_args.kwargs['fallback'], {'provider': 'gemini', 'model': 'gemini/test'})

    def test_back_skips_prompts_supplied_by_launch_flags(self):
        seen, apply, _, _ = self.run_keys('\r\r\x1b\rskip\r',
                                         ['--effort', 'high', '--autocompact', '80', '--resume', SESSION])
        self.assertEqual([name for name, _ in seen],
                         ['Provider', 'Model', 'Subagent provider', 'Model', 'Subagent provider'])
        apply.assert_called_once_with('official', 'official/test', False,
                                      claude_args=['--effort', 'high', '--resume', SESSION],
                                      effort=None, autocompact=80, fallback=None)

    def test_subagent_provider_back_returns_to_profile_selection(self):
        agents = {'one': {}, 'two': {}}
        seen, apply, save, _ = self.run_keys('\r' * 5 + '\x1btwo\rskip\r', agents=agents)
        self.assertEqual([name for name, _ in seen], self.PROMPTS[:4] +
                         ['Subagent', 'Subagent provider', 'Subagent', 'Subagent provider'])
        apply.assert_called_once()
        save.assert_not_called()

    def test_agent_editor_back_returns_to_action_then_profile_list(self):
        seen, apply, save, _ = self.run_keys('alpha\r\r\x1b\x1bdone\r', ['agents'], {'alpha': {}})
        self.assertEqual([name for name, _ in seen],
                         ['Subagents', 'alpha', 'Subagent provider', 'alpha', 'Subagents'])
        apply.assert_not_called()
        save.assert_not_called()

    def test_subagent_answers_survive_backtracking_into_main_settings(self):
        keys = '\r' * 4 + 'nvidia\r\rhigh\r' + '\x1b' * 4 + '\r\x03'
        seen, apply, save, _ = self.run_keys(keys)
        self.assertEqual([default for name, default in seen if name == 'Subagent provider'],
                         ['official', 'nvidia', 'nvidia'])
        apply.assert_not_called()
        save.assert_not_called()

    def test_profile_draft_survives_backtracking_through_action_menu(self):
        keys = 'alpha\r\rnvidia\r' + '\x1b' * 2 + '\r\x03'
        seen, apply, save, _ = self.run_keys(keys, ['agents'], {'alpha': {}})
        self.assertEqual([default for name, default in seen if name == 'Subagent provider'],
                         ['official', 'nvidia', 'nvidia'])
        apply.assert_not_called()
        save.assert_not_called()

    def test_profile_selection_survives_backtracking_into_main_settings(self):
        keys = '\r' * 4 + 'two\r' + '\x1b' * 2 + '\r\x03'
        seen, apply, save, _ = self.run_keys(keys, agents={'one': {}, 'two': {}})
        self.assertEqual([default for name, default in seen if name == 'Subagent'],
                         ['one', 'two', 'two'])
        apply.assert_not_called()
        save.assert_not_called()

    def test_editor_list_keeps_selected_profile_when_action_goes_back(self):
        seen, apply, save, _ = self.run_keys('alpha\r\x1b\x03', ['agents'], {'alpha': {}})
        self.assertEqual([default for name, default in seen if name == 'Subagents'],
                         ['done', 'alpha'])
        apply.assert_not_called()
        save.assert_not_called()


class FallbackTests(unittest.TestCase):
    def test_fallback_is_off_until_set_and_per_launch(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(cc, 'FALLBACKS_FILE', os.path.join(tmp, 'f.json')), \
                patch.dict(os.environ, {'CC_LAUNCH_ID': 'one', 'CC_MAIN_PROVIDER': 'gemini'}), contextlib.redirect_stdout(io.StringIO()) as out:
            cc.fallback_command([])
            cc.fallback_command(['NVIDIA', 'deepseek-ai/deepseek-v4-pro'])
            os.environ['CC_LAUNCH_ID'] = 'two'
            cc.fallback_command([])
            os.environ['CC_LAUNCH_ID'] = 'one'
            cc.fallback_command(['off'])
            self.assertEqual(out.getvalue().splitlines(), ['Fallback: off', 'Fallback: nvidia/deepseek-ai/deepseek-v4-pro', 'Fallback: off', 'Fallback: off'])
            with self.assertRaises(SystemExit):
                cc.fallback_command(['official', 'claude-opus-5-5'])  # not an adapter route

    def test_fallback_and_route_keep_an_effort(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(cc, 'FALLBACKS_FILE', os.path.join(tmp, 'f.json')),                 patch.object(cc, 'ROUTES_FILE', os.path.join(tmp, 'r.json')),                 patch.dict(os.environ, {'CC_LAUNCH_ID': 'one', 'CC_MAIN_PROVIDER': 'gemini'}), contextlib.redirect_stdout(io.StringIO()) as out:
            cc.fallback_command(['nvidia', 'x/y', 'high'])
            cc.route_command(['nvidia', 'x/y', 'default'])
            cc.route_command(['nvidia', 'x/y', 'low'])
            self.assertEqual(out.getvalue().splitlines(), ['Fallback: nvidia/x/y', 'Effort: high', 'Route: nvidia/x/y',
                                                           'Route: nvidia/x/y', 'Effort: low'])
            with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
                cc.route_command(['nvidia', 'x/y', 'lo w;'])

    def test_picker_lists_routes_without_the_session_own(self):
        with patch.dict(os.environ, {'CC_MAIN_PROVIDER': 'nvidia', 'CC_LAUNCH_ID': ''}), contextlib.redirect_stdout(io.StringIO()) as out:
            cc.fallback_command(['routes'])
        names = [line.split('\t')[0] for line in out.getvalue().splitlines()]
        self.assertEqual(names, [n for n in cc.FALLBACK_ROUTES if n != 'nvidia'])
        with patch.object(cc, 'get_models', return_value=[{'id': 'a'}, {'id': 'b'}]), contextlib.redirect_stdout(io.StringIO()) as out:
            cc.fallback_command(['models', 'gemini'])
        self.assertEqual(out.getvalue().split(), ['a', 'b'])

    def test_fallback_needs_a_cc_launch(self):
        with patch.dict(os.environ, {'CC_LAUNCH_ID': ''}), self.assertRaises(SystemExit):
            cc.fallback_command([])

    def test_slash_fallback_is_answered_without_a_model_turn(self):
        with patch.object(cc.subprocess, 'run', return_value=Mock(stdout='Fallback: off', stderr='')) as run:
            out = cc.hook_command({'command_name': 'fallback', 'command_args': 'off'})
        self.assertEqual(run.call_args.args[0][-2:], ['fallback', 'off'])
        self.assertEqual(out['decision'], 'block')
        self.assertIn('Fallback: off', out['reason'])


if __name__ == '__main__':
    unittest.main()
