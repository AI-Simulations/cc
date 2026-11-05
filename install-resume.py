"""Install ccx into ~/.config/cc and register its shell function, with backups.

Windows: PowerShell 5 and 7 profiles. macOS/Linux: ~/.bashrc and ~/.zshrc.
"""
from datetime import datetime
from pathlib import Path
import json
import os
import re
import shutil
import sys

ROOT = Path(__file__).resolve().parent
FILES = ('cc-switch.py', 'cc-switch.ps1', 'cc-anthropic-proxy.py', 'opencode_key.py', 'patch-oauth-quota.py',
         'oauth-errors.mjs', 'test_cc_switch.py', 'test_cc_resume.py', 'test_cc_proxy.py',
         'statusline.py', 'test_statusline.py', 'critic_mock_check.py', 'install-resume.py', 'README.md',
         'cc-gui.py', 'test_cc_gui.py', 'cc.ico')
# Claude Code slash commands shipped with cc.
COMMAND_FILES = ('fallback.md',)
# The /model picker mod, loaded through CLAUDE_CODE_PLUGIN_DIRS.
PLUGIN = 'subagent-picker'
PYTHON = 'python' if os.name == 'nt' else 'python3'
FUNCTION = b'''\r\n# Claude Code custom-provider launcher.\r\nfunction ccx {\r\n    & "$env:USERPROFILE\\.config\\cc\\cc-switch.ps1" @args\r\n}\r\n'''
SH_FUNCTION = '''
# Claude Code custom-provider launcher.
ccx() {
    python3 "$HOME/.config/cc/cc-switch.py" "$@" || return
    if [ -f "$HOME/.config/cc/apply.sh" ]; then
        . "$HOME/.config/cc/apply.sh"
        rm -f "$HOME/.config/cc/apply.sh"
    fi
}
'''

ALIAS_GUARD = '''
# Keep the custom-provider launcher ahead of a legacy cc -> claude alias.
if ((Test-Path Alias:cc) -and (Get-Alias cc).Definition -eq 'claude') {
    Remove-Item Alias:cc -Force
}
'''


def copy_tree(source, target, backup):
    for path in source.rglob('*'):
        relative = path.relative_to(source)
        if not path.is_file() or any(part == '__pycache__' or part.endswith(('.pyc', '.local.json')) or 'state' in part
                                     for part in relative.parts):
            continue
        destination = target / relative
        if destination.exists():
            saved = backup / target.name / relative
            saved.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(destination, saved)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)


def install(target, profiles, commands=None, claude=None):
    target = Path(target).resolve()
    # Validate user settings before changing any installed files.
    settings = None
    if claude is not None:
        claude = Path(claude).resolve()
        settings_path = claude / 'settings.json'
        settings = json.loads(settings_path.read_text(encoding='utf-8-sig')) if settings_path.exists() else {}
        if not isinstance(settings, dict) or not isinstance(settings.get('hooks', {}), dict):
            raise ValueError('Claude settings must contain objects, not arrays or strings')
    backup = target / ('backup-resume-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
    backup.mkdir(parents=True)
    for name in FILES:
        destination = target / name
        if destination.exists():
            shutil.copy2(destination, backup / name)
        shutil.copy2(ROOT / name, destination)
        assert destination.read_bytes() == (ROOT / name).read_bytes(), name
    if commands:
        commands.mkdir(parents=True, exist_ok=True)
        for name in COMMAND_FILES:
            destination = commands / name
            if destination.exists():
                saved = backup / 'commands' / name
                saved.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(destination, saved)
            shutil.copy2(ROOT / 'commands' / name, destination)
            assert destination.read_bytes() == (ROOT / 'commands' / name).read_bytes(), name
    if (ROOT / PLUGIN).is_dir():
        copy_tree(ROOT / PLUGIN, target / PLUGIN, backup)
    if settings is not None:
        claude.mkdir(parents=True, exist_ok=True)
        saved = backup / 'claude'
        saved.mkdir()
        for name in ('settings.json', 'statusline.py'):
            if (claude / name).exists():
                shutil.copy2(claude / name, saved / name)
        shutil.copy2(ROOT / 'statusline.py', claude / 'statusline.py')
        command = PYTHON + ' -B "' + (target / 'cc-switch.py').as_posix() + '" '
        hooks = settings.setdefault('hooks', {})
        for event, matcher, entry in [('PreToolUse', 'Agent|Task', 'hook-agent'),
                                      ('SessionStart', None, 'hook-context'),
                                      ('UserPromptSubmit', None, 'hook-context'),
                                      ('SubagentStop', '^cc-.*', 'hook-subagent-stop'),
                                      ('UserPromptExpansion', 'fallback', 'hook-command')]:
            groups = hooks.setdefault(event, [])
            # Replace only our own entries; preserve every unrelated hook and matcher.
            for group in groups:
                group['hooks'] = [h for h in group.get('hooks', []) if not (
                    'cc-switch.py' in h.get('command', '') and h.get('command', '').rstrip().endswith(entry))]
            groups[:] = [group for group in groups if group.get('hooks')]
            group = {'hooks': [{'type': 'command', 'command': command + entry, 'timeout': 20}]}
            if matcher:
                group['matcher'] = matcher
            groups.append(group)
        settings.setdefault('statusLine', {}).update(
            type='command', command=PYTHON + ' -B "' + (claude / 'statusline.py').as_posix() + '"')
        settings['subagentStatusLine'] = {'type': 'command', 'command': command + 'agent-statusline'}
        if (ROOT / PLUGIN).is_dir():
            env = settings.setdefault('env', {})
            plugin = (target / PLUGIN).as_posix()
            dirs = [d for d in str(env.get('CLAUDE_CODE_PLUGIN_DIRS') or '').split(os.pathsep) if d]
            if plugin not in dirs:
                env['CLAUDE_CODE_PLUGIN_DIRS'] = os.pathsep.join([*dirs, plugin])
        temporary = settings_path.with_suffix('.json.tmp')
        temporary.write_text(json.dumps(settings, indent=2) + '\n', encoding='utf-8')
        temporary.replace(settings_path)
    # The vendored package is generated by ai-suite/sync.py, never edited here.
    copy_tree(ROOT / 'ai_suite', target / 'ai_suite', backup)
    for profile in profiles:
        register(profile, backup)
    print('Installed ccx. Open a new terminal, then run: ccx   Backup:', backup)


def register(profile, backup):
    """Add the ccx function to one shell profile, once."""
    original = profile.read_bytes() if profile.exists() else b''
    if profile.suffix == '.ps1':
        encoding = ('utf-16-le' if original.startswith(b'\xff\xfe') else
                    'utf-16-be' if original.startswith(b'\xfe\xff') else
                    'utf-8' if original.startswith(b'\xef\xbb\xbf') else 'latin-1')
        text = original.decode(encoding).lstrip('﻿')
        addition = '' if re.search(r'(?im)^\s*function\s+ccx\s*\{', text) else FUNCTION.decode('ascii')
        if (addition or re.search(r'(?im)^\s*(?:Set-Alias|New-Alias|sal|nal)\b[^\r\n]*\bcc\b', text)) and ALIAS_GUARD.strip() not in text:
            addition += ALIAS_GUARD
    else:
        encoding = 'utf-8'
        text = original.decode(encoding, errors='replace')
        addition = '' if re.search(r'(?m)^\s*ccx\s*\(\)', text) else SH_FUNCTION
    if not addition:
        return
    profile.parent.mkdir(parents=True, exist_ok=True)
    if profile.exists():
        shutil.copy2(profile, backup / (profile.parent.name + '-' + profile.name))
    profile.write_bytes(original + addition.encode(encoding))
    print('Registered ccx:', profile)


def shell_profiles(home):
    if os.name == 'nt':
        return [home / 'Documents' / shell / 'Microsoft.PowerShell_profile.ps1'
                for shell in ('WindowsPowerShell', 'PowerShell')]
    # bash and zsh; macOS's default shell is zsh, most Linux desktops use bash.
    return [home / '.bashrc', home / '.zshrc']


def main():
    if sys.version_info < (3, 10):
        sys.exit('ccx needs Python 3.10 or newer.')
    if not shutil.which('claude'):
        print('Note: `claude` is not on PATH yet; install Claude Code before running ccx.')
    home = Path.home()
    commands = home / '.claude' / 'commands'
    commands.mkdir(parents=True, exist_ok=True)
    install(home / '.config/cc', shell_profiles(home), commands, home / '.claude')


if __name__ == '__main__':
    main()
