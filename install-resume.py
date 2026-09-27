"""Install cc and register missing PowerShell profiles, with backups."""
from datetime import datetime
from pathlib import Path
import re
import shutil

ROOT = Path(__file__).resolve().parent
FILES = ('cc-switch.py', 'cc-switch.ps1', 'cc-anthropic-proxy.py',
         'test_cc_switch.py', 'test_cc_resume.py', 'test_cc_proxy.py',
         'critic_mock_check.py', 'install-resume.py', 'README.md')
FUNCTION = b'''\r\n# Claude Code custom-provider launcher.\r\nfunction cc {\r\n    & "$env:USERPROFILE\\.config\\cc\\cc-switch.ps1" @args\r\n}\r\n'''


def install(target, profiles):
    backup = target / ('backup-resume-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
    backup.mkdir(parents=True)
    for name in FILES:
        destination = target / name
        if destination.exists():
            shutil.copy2(destination, backup / name)
        shutil.copy2(ROOT / name, destination)
        assert destination.read_bytes() == (ROOT / name).read_bytes(), name
    for profile in profiles:
        original = profile.read_bytes() if profile.exists() else b''
        encoding = ('utf-16-le' if original.startswith(b'\xff\xfe') else
                    'utf-16-be' if original.startswith(b'\xfe\xff') else
                    'utf-8' if original.startswith(b'\xef\xbb\xbf') else 'latin-1')
        if re.search(r'(?im)^\s*function\s+cc\s*\{', original.decode(encoding).lstrip('\ufeff')):
            continue
        profile.parent.mkdir(parents=True, exist_ok=True)
        if profile.exists():
            shutil.copy2(profile, backup / (profile.parent.name + '-' + profile.name))
        profile.write_bytes(original + FUNCTION.decode('ascii').encode(encoding))
        print('Registered cc:', profile)
    print('Installed cc resume support. Backup:', backup)


if __name__ == '__main__':
    home = Path.home()
    install(home / '.config/cc', [home / 'Documents' / shell / 'Microsoft.PowerShell_profile.ps1'
                                for shell in ('WindowsPowerShell', 'PowerShell')])
