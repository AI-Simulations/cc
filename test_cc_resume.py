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


class ResumeTests(unittest.TestCase):
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

    def test_resume_cli(self):
        cases = [
            (['--resume', SESSION], 'nvidia', 'test/model', ['--resume', SESSION]),
            (['--resume'], 'nvidia', 'test/model', ['--resume']),
            (['-r', 'named session'], 'nvidia', 'test/model', ['-r', 'named session']),
            (['--continue'], 'nvidia', 'test/model', ['--continue']),
            (['-c'], 'nvidia', 'test/model', ['-c']),
            (['official', '--resume', SESSION], 'official', None, ['--resume', SESSION]),
            (['nvidia', 'other/model', '--resume', SESSION], 'nvidia', 'other/model', ['--resume', SESSION]),
            (['nvidia', '--resume', SESSION], 'nvidia', 'test/model', ['--resume', SESSION]),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            for argv, provider, model, forwarded in cases:
                with self.subTest(argv=argv), patch.object(cc, 'APPLY_FILE', str(Path(tmp)/'apply.ps1')), patch.object(cc, 'vt_enable'), patch.object(cc, 'load_state', return_value={'provider': 'nvidia', 'model': 'test/model'}), patch.object(cc, 'apply') as apply, patch.object(cc, 'provider_menu', side_effect=AssertionError('Unexpected menu')), patch.object(cc, 'model_menu', side_effect=AssertionError('Unexpected model menu')), patch.object(sys, 'argv', ['cc', *argv]), contextlib.redirect_stdout(io.StringIO()):
                    cc.main()
                    apply.assert_called_once_with(provider, model, False, claude_args=forwarded)

    def test_resume_without_saved_provider_uses_menu(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(cc, 'APPLY_FILE', str(Path(tmp)/'apply.ps1')), patch.object(cc, 'vt_enable'), patch.object(cc, 'load_state', return_value={}), patch.object(cc, 'provider_menu', return_value='official') as menu, patch.object(cc, 'apply') as apply, patch.object(sys, 'argv', ['cc', '--resume', SESSION]):
            cc.main()
            menu.assert_called_once()
            apply.assert_called_once_with('official', None, False, claude_args=['--resume', SESSION])

    def test_quoted_arguments_reach_claude(self):
        tricky = "session'$(throw 'must not execute'); with spaces"
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp)/'apply.ps1'
            with patch.object(cc, 'APPLY_FILE', str(script)):
                cc.write_apply({'ANTHROPIC_MODEL': 'test/model'}, [], True, ['--resume', tricky])
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
