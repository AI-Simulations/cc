"""macOS/Linux launcher checks: apply.sh through a real shell, quoting, and the arrow-key reader.
Offline; skipped on Windows (test_ccx_switch.py covers PowerShell there)."""
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('ccx_switch', ROOT / 'ccx-switch.py')
ccx = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ccx)


@unittest.skipIf(os.name == 'nt', 'POSIX shells only')
class PosixApplyTests(unittest.TestCase):
    def source(self, env, delete, run_claude, claude_args=(), model=None):
        """Write apply.sh, source it in sh with a fake `claude`, and return what the shell saw."""
        with tempfile.TemporaryDirectory() as tmp:
            apply = Path(tmp) / 'apply.sh'
            with patch.object(ccx, 'APPLY_FILE', str(apply)):
                ccx.write_apply(env, delete, run_claude, claude_args, model)
            script = (f'claude() {{ printf "claude:%s|" "$@"; printf "base:%s\\n" "$ANTHROPIC_BASE_URL"; }}\n'
                      f'. "{apply}"\nprintf "after:%s|stale:%s\\n" "$ANTHROPIC_BASE_URL" "${{STALE-unset}}"\n')
            result = subprocess.run(['sh', '-c', script], capture_output=True, text=True, check=True,
                                    env=dict(os.environ, STALE='old', ANTHROPIC_BASE_URL='before'))
            return result.stdout

    def test_no_run_exports_into_the_calling_shell(self):
        out = self.source({'ANTHROPIC_BASE_URL': 'http://127.0.0.1:10532/x'}, ['STALE'], False)
        self.assertIn('after:http://127.0.0.1:10532/x|stale:unset', out)

    def test_launch_scopes_variables_to_a_subshell(self):
        out = self.source({'ANTHROPIC_BASE_URL': 'http://route'}, ['STALE'], True, ['--resume', 'a b'], 'm1')
        self.assertIn("claude:--model|claude:m1|claude:--resume|claude:a b|base:http://route", out)
        self.assertIn('after:before|stale:old', out, 'a later plain claude keeps the default login')

    def test_values_cannot_run_shell_code(self):
        hostile = "x'; touch pwned; echo '$(touch pwned2)`touch pwned3`"
        with tempfile.TemporaryDirectory() as tmp:
            cwd = os.getcwd()
            os.chdir(tmp)
            try:
                out = self.source({'ANTHROPIC_MODEL': hostile}, [], True, [hostile], hostile)
            finally:
                os.chdir(cwd)
            self.assertEqual(sorted(os.listdir(tmp)), [])
        self.assertIn('claude:' + hostile, out)

    def test_installed_function_sources_and_removes_apply(self):
        bash = shutil.which('bash')
        if not bash:
            self.skipTest('no bash')
        spec = importlib.util.spec_from_file_location('ccx_install', ROOT / 'install-resume.py')
        installer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(installer)
        with tempfile.TemporaryDirectory() as home:
            cfg = Path(home) / '.config' / 'ccx'
            cfg.mkdir(parents=True)
            # A stand-in launcher: writes apply.sh the way ccx-switch.py does, then exits 0.
            (cfg / 'ccx-switch.py').write_text(
                'import os\nopen(os.path.expanduser("~/.config/ccx/apply.sh"), "w").write("export PICKED=yes\\n")\n')
            script = installer.SH_FUNCTION + 'ccx official\nprintf "%s|" "$PICKED"; [ -f ~/.config/ccx/apply.sh ] || echo gone\n'
            out = subprocess.run([bash, '-c', script], capture_output=True, text=True,
                                 env=dict(os.environ, HOME=home)).stdout
        self.assertEqual(out.strip(), 'yes|gone')


@unittest.skipIf(os.name == 'nt', 'POSIX terminals only')
class KeyReaderTests(unittest.TestCase):
    def test_arrows_backspace_and_enter_read_like_the_windows_console(self):
        import pty
        pid, fd = pty.fork()
        if pid == 0:  # child: read five keys through the menu's reader and print them
            sys.path.insert(0, str(ROOT))
            import ai_suite.providers as providers
            read = providers._key_reader()
            print(repr([read() for _ in range(6)]), flush=True)
            os._exit(0)
        time.sleep(0.5)
        os.write(fd, b'\x1b[B\x7fa\nb')
        out, end = b'', time.time() + 5
        while time.time() < end and b']' not in out:
            try:
                out += os.read(fd, 1024)
            except OSError:
                break
        os.waitpid(pid, 0)
        self.assertIn(repr(['\xe0', 'P', '\x08', 'a', '\r', 'b']), out.decode(errors='replace'))


if __name__ == '__main__':
    unittest.main()
