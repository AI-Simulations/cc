"""Offline CLI and PowerShell resume regression checks."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('cc_resume_switch', ROOT / 'cc-switch.py')
cc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cc)
SESSION = '00000000-0000-4000-8000-000000000001'


def runpy_upstreams():
    spec = importlib.util.spec_from_file_location('cc_proxy', ROOT / 'cc-anthropic-proxy.py')
    proxy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(proxy)
    return proxy.UPSTREAMS


class ResumeTests(unittest.TestCase):
    def test_adapter_launch_has_no_console(self):
        with patch.object(cc, 'fetch', side_effect=[OSError(), {'service': 'cc-anthropic-proxy', 'version': 9}]), patch.object(cc.subprocess, 'Popen') as launch:
            cc.ensure_anthropic_proxy()
        self.assertEqual(launch.call_args.kwargs['creationflags'], subprocess.CREATE_NO_WINDOW)

    def test_outdated_adapter_is_replaced_and_other_services_are_left_alone(self):
        current = {'service': 'cc-anthropic-proxy', 'version': 9}
        with patch.object(cc, 'fetch', side_effect=[{'service': 'cc-anthropic-proxy', 'version': 1}, current]), patch.object(cc, 'stop_port_owner') as stop, patch.object(cc.subprocess, 'Popen') as launch:
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
            with contextlib.redirect_stdout(io.StringIO()):
                installer.install(target, [existing, missing])
                first = missing.read_bytes()
                installer.install(target, [existing, missing])
            self.assertEqual(existing.read_bytes(), original)
            self.assertEqual(missing.read_bytes(), first)
            self.assertIn(b'function cc {', first)
            self.assertIn(b'cc-switch.ps1" @args', first)
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
                with self.subTest(argv=argv), patch.object(cc, 'APPLY_FILE', str(Path(tmp)/'apply.ps1')), patch.object(cc, 'load_state', return_value={'provider': 'nvidia', 'model': 'test/model'}), patch.object(cc, 'apply') as apply, patch.object(cc, 'provider_menu', return_value='official') as menu, patch.object(cc, 'model_menu', return_value='claude-opus-5-5') as models, patch.object(cc, 'effort_menu', return_value='high'), patch.object(sys, 'argv', ['cc', *argv]):
                    cc.main()
                    menu.assert_called_once()
                    models.assert_called_once_with('official')
                    apply.assert_called_once_with('official', 'claude-opus-5-5', False, claude_args=forwarded, effort='high')

    def test_explicit_provider_and_model_skip_menus(self):
        cases = [
            (['official', 'opus', '--resume', SESSION], 'official', 'opus'),
            (['nvidia', 'other/model', '--resume', SESSION], 'nvidia', 'other/model'),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            for argv, provider, model in cases:
                with self.subTest(argv=argv), patch.object(cc, 'APPLY_FILE', str(Path(tmp)/'apply.ps1')), patch.object(cc, 'apply') as apply, patch.object(cc, 'provider_menu', side_effect=AssertionError('Unexpected menu')), patch.object(cc, 'model_menu', side_effect=AssertionError('Unexpected model menu')), patch.object(sys, 'argv', ['cc', *argv]):
                    cc.main()
                    apply.assert_called_once_with(provider, model, False, claude_args=['--resume', SESSION], effort=None)

    def test_provider_only_still_asks_model(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(cc, 'APPLY_FILE', str(Path(tmp)/'apply.ps1')), patch.object(cc, 'provider_menu', side_effect=AssertionError('Unexpected menu')), patch.object(cc, 'model_menu', return_value='test/model') as models, patch.object(cc, 'effort_menu', return_value='') as efforts, patch.object(cc, 'apply') as apply, patch.object(sys, 'argv', ['cc', 'nvidia', '--resume', SESSION]):
            cc.main()
            models.assert_called_once_with('nvidia')
            efforts.assert_called_once()
            apply.assert_called_once_with('nvidia', 'test/model', False, claude_args=['--resume', SESSION], effort='')

    def test_launch_env_is_restored_so_plain_claude_stays_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp)/'apply.ps1'
            with patch.object(cc, 'APPLY_FILE', str(script)):
                cc.write_apply({'ANTHROPIC_BASE_URL': 'http://gateway', 'ANTHROPIC_MODEL': 'test/model'}, ['ANTHROPIC_API_KEY'], True, [], 'test/model')
            check = Path(tmp)/'check.ps1'
            check.write_text("$env:ANTHROPIC_API_KEY = 'original'\nfunction claude { \"during=$env:ANTHROPIC_BASE_URL|$env:ANTHROPIC_API_KEY\" }\n. $env:CC_TEST_APPLY\n\"after=$env:ANTHROPIC_BASE_URL|$env:ANTHROPIC_MODEL|$env:ANTHROPIC_API_KEY\"\n", encoding='utf-8')
            env = {k: v for k, v in os.environ.items() if not k.startswith('ANTHROPIC_')}
            result = subprocess.run(['powershell', '-NoProfile', '-File', str(check)], env=dict(env, CC_TEST_APPLY=str(script)), capture_output=True, text=True, check=True)
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
            result = subprocess.run(['powershell', '-NoProfile', '-File', str(check)], env=dict(os.environ, CC_TEST_APPLY=str(script)), capture_output=True, text=True, check=True)
            self.assertEqual(json.loads(result.stdout.splitlines()[-1]), ['--model', 'test/model', '--resume', tricky])
            self.assertFalse(script.exists())

    def test_compatibility_entry_forwards_native_flags(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shutil.copyfile(ROOT/'cc-switch.ps1', root/'cc-switch.ps1')
            (root/'cc-switch.py').write_text("import json, sys\nfrom pathlib import Path\nPath(__file__).with_name('args.json').write_text(json.dumps(sys.argv[1:]))\n", encoding='utf-8')
            result = subprocess.run(['powershell', '-NoProfile', '-File', str(root/'cc-switch.ps1'), '--resume', SESSION, '--continue'], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads((root/'args.json').read_text()), ['--resume', SESSION, '--continue'])


if __name__ == '__main__':
    unittest.main()
