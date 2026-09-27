#!/usr/bin/env python3
# cc-switch.py - Claude Code provider switcher (menu UI, stdlib only)
# Usage:
#   cc                     interactive menu, then opens Claude Code
#   cc <provider> [model]  e.g. cc official | cc minimax | cc opencode-go
#   cc -NoRun <provider>   switch without launching a client
#   cc status              show current session state
# Only affects the current PowerShell session: env vars are applied by the
# 'cc' PS function dot-sourcing apply.ps1 (a child process cannot set them).

import ctypes, json, os, re, socket, subprocess, sys, time, urllib.request, winreg
import msvcrt

CFG_DIR = os.path.expanduser(r"~\.config\cc")
APPLY_FILE = os.path.join(CFG_DIR, "apply.ps1")
STATE_FILE = os.path.join(CFG_DIR, "state.json")
OPENCODE_AUTH = os.path.expanduser(r"~\.local\share\opencode\auth.json")
OPENCODE_CONFIG = os.path.expanduser(r"~\.config\opencode\opencode.jsonc")
OAUTH_PORT = 10531
ADAPTER_PORT = 10532

CC_VARS = ["ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY",
           "ANTHROPIC_MODEL", "ANTHROPIC_DEFAULT_OPUS_MODEL", "ANTHROPIC_DEFAULT_SONNET_MODEL",
           "ANTHROPIC_DEFAULT_HAIKU_MODEL", "ANTHROPIC_DEFAULT_FABLE_MODEL", "CLAUDE_CODE_SUBAGENT_MODEL", "ANTHROPIC_CUSTOM_HEADERS", "ENABLE_TOOL_SEARCH", "CLAUDE_CODE_MAX_CONTEXT_TOKENS", "CLAUDE_CODE_AUTO_MODE_SERVER"]
OAI_VARS = ["OPENAI_BASE_URL", "OPENAI_API_KEY"]

PROVIDERS = {
    "commandcode": {"label": "Claude Code via Command Code", "format": "anthropic", "base": "https://api.commandcode.ai/provider", "key": "commandcode"},
    "official":     {"label": "Claude official (claude.ai login)", "format": "anthropic", "base": ""},
    "minimax":      {"label": "MiniMax (Anthropic API)", "format": "anthropic", "base": "https://api.minimax.io/anthropic", "keyEnv": "MINIMAX_API_KEY"},
    "opencode-go":  {"label": "OpenCode Go subscription (x-api-key, live key)", "format": "anthropic", "base": "https://opencode.ai/zen/go", "key": "opencode-go", "header": "x-api-key"},
    "opencode-zen": {"label": "OpenCode Zen PAYG (needs a zen key; unverified)", "format": "anthropic", "base": "https://opencode.ai/zen", "key": "opencode", "header": "x-api-key"},
    "openai-oauth": {"label": "OpenAI OAuth proxy - ChatGPT account (OpenAI-format)", "format": "openai", "base": f"http://127.0.0.1:{OAUTH_PORT}/v1", "key": "oauth"},
    "openrouter":   {"label": "Claude Code via OpenRouter (API credits)", "format": "anthropic", "base": "https://openrouter.ai/api", "key": "openrouter", "auth": "bearer"},
    "nvidia":       {"label": "Claude Code via NVIDIA NIM", "format": "openai", "base": "https://integrate.api.nvidia.com/v1", "key": "nvidia"},
}

# go and zen are DIFFERENT gateways with different model catalogs
FALLBACK_MODELS = {
    "commandcode": [("claude-sonnet-4-6", "")],
    "opencode-go":  [("deepseek-v4-flash", "DeepSeek V4 Flash"), ("deepseek-v4-pro", "DeepSeek V4 Pro"),
                     ("glm-5.2", "GLM-5.2"), ("kimi-k2.7-code", "Kimi K2.7 Code"),
                     ("qwen3.7-max", "Qwen3.7 Max"), ("minimax-m3", "MiniMax M3")],
    "opencode-zen": [("claude-opus-5", ""), ("claude-sonnet-5", ""), ("claude-fable-5", "")],
    "openrouter":   [("openai/gpt-5.3-codex", ""), ("anthropic/claude-sonnet-4.6", "")],
    "nvidia":       [("nvidia/llama-3.3-nemotron-super-49b-v1", "")],
    "openai-oauth": [("gpt-5.4-mini", "")],
}


def die(msg):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def warn(msg):
    print(f"  WARNING: {msg}", file=sys.stderr)


def user_env(name):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
            v, _ = winreg.QueryValueEx(k, name)
            return v
    except OSError:
        return ""


def auth_key(name):
    # Only read this provider's credentials; never send another provider's key.
    env_name = "CMD_API_KEY" if name == "commandcode" else name.upper().replace("-", "_") + "_API_KEY"
    key = os.environ.get(env_name) or user_env(env_name)
    if key:
        return key
    if name == "commandcode":
        try:
            with open(os.path.expanduser(r"~\.commandcode\auth.json"), encoding="utf-8-sig") as f:
                return json.load(f).get("apiKey", "")
        except (OSError, ValueError):
            return ""
    try:
        auth = json.load(open(OPENCODE_AUTH, encoding="utf-8"))
    except Exception:
        auth = {}
    for c in [name]:
        e = auth.get(c)
        if e and e.get("type") == "api" and e.get("key"):
            return e["key"]
    return ""


def load_state():
    try:
        return json.load(open(STATE_FILE, encoding="utf-8"))
    except Exception:
        return {}


def save_state(data):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f)


def fetch(url, headers=None, timeout=15):
    h = {"User-Agent": "Mozilla/5.0 cc-switch"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def go_limits():
    try:
        cfg = json.load(open(OPENCODE_CONFIG, encoding="utf-8"))
        return {n: (v.get("limit") or {}) for n, v in cfg["provider"]["opencode-go"]["models"].items()}
    except Exception:
        return {}


def get_models(name):
    models = []
    try:
        if name == "commandcode":
            live = fetch("https://api.commandcode.ai/provider/v1/models", {"Authorization": "Bearer " + auth_key(name)})
            for m in live["data"]:
                if any(endpoint.endswith("/messages") for endpoint in m.get("supported_endpoints", [])):
                    models.append({"id": m["id"], "name": m.get("name", ""), "context": m.get("context_window") or m.get("context_length"), "output": None})
        elif name == "opencode-go":
            live = fetch("https://opencode.ai/zen/go/v1/models", {"x-api-key": auth_key("opencode-go")})
            limits = go_limits()
            for m in live["data"]:
                lim = limits.get(m["id"], {})
                models.append({"id": m["id"], "name": "", "context": lim.get("context"), "output": lim.get("output")})
            models.sort(key=lambda m: m["output"] or 0, reverse=True)
        elif name == "opencode-zen":
            live = fetch("https://opencode.ai/zen/v1/models", {"x-api-key": auth_key("opencode")})
            for m in live["data"]:
                if re.search(r"image|tts|embed", m["id"]):
                    continue
                models.append({"id": m["id"], "name": "", "context": m.get("context_window") or m.get("context_length"), "output": None})
        elif name == "openrouter":
            live = fetch("https://openrouter.ai/api/v1/models")
            for m in live["data"]:
                if re.search(r"image|tts|embed", m["id"]):
                    continue
                models.append({"id": m["id"], "name": m.get("name", ""),
                               "context": m.get("context_length"), "output": m.get("top_provider", {}).get("max_completion_tokens")})
        elif name == "nvidia":
            live = fetch("https://integrate.api.nvidia.com/v1/models",
                         {"Authorization": f"Bearer {auth_key('nvidia')}"})
            for m in live["data"]:
                if re.search(r"image|tts|embed", m["id"]):
                    continue
                models.append({"id": m["id"], "name": "", "context": m.get("context_window") or m.get("context_length"), "output": None})
        elif name == "openai-oauth":
            ensure_oauth_proxy()
            live = fetch(f"http://127.0.0.1:{OAUTH_PORT}/v1/models", timeout=10)
            # OAuth route limits can differ from the public API model limits.
            try:
                codex_home = os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex")
                with open(os.path.join(codex_home, "models_cache.json"), encoding="utf-8") as f:
                    limits = {m["slug"]: m.get("max_context_window") or m.get("context_window")
                              for m in json.load(f)["models"]}
            except (OSError, ValueError, KeyError, TypeError):
                limits = {}
            for m in live["data"]:
                if re.search(r"image|tts|embed", m["id"]):
                    continue
                models.append({"id": m["id"], "name": "", "context": m.get("context_window") or limits.get(m["id"]), "output": m.get("max_output_tokens")})
    except Exception as e:
        print(f"  (model fetch failed: {e} - using fallback)", file=sys.stderr)

    if not models:
        models = [{"id": i, "name": n, "context": None, "output": None} for i, n in FALLBACK_MODELS.get(name, [])]
    return models


def oauth_proxy_up():
    try:
        socket.create_connection(("127.0.0.1", OAUTH_PORT), timeout=0.5).close()
        return True
    except OSError:
        return False


def ensure_oauth_proxy():
    if oauth_proxy_up():
        return
    if not subprocess.run("where npx >nul 2>nul", shell=True).returncode == 0:
        die("openai-oauth needs Node.js (npx) on PATH.")
    print("Starting openai-oauth proxy; complete browser sign-in if prompted...", file=sys.stderr)
    subprocess.Popen("npx openai-oauth --detach", shell=True,
                     creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)
    for _ in range(40):
        if oauth_proxy_up():
            return
        time.sleep(0.25)
    die(f"openai-oauth proxy did not open port {OAUTH_PORT}.")


def ensure_anthropic_proxy():
    url = f"http://127.0.0.1:{ADAPTER_PORT}/health"
    expected = {"service": "cc-anthropic-proxy", "version": 1}
    try:
        if fetch(url, timeout=1) == expected:
            return
        die(f"Port {ADAPTER_PORT} is occupied by another service.")
    except (OSError, ValueError):
        pass
    subprocess.Popen([sys.executable, os.path.join(CFG_DIR, "cc-anthropic-proxy.py")],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     creationflags=subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS)
    for _ in range(40):
        try:
            if fetch(url, timeout=1) == expected:
                return
        except (OSError, ValueError):
            pass
        time.sleep(0.1)
    die(f"Claude Messages adapter did not start on port {ADAPTER_PORT}.")


# ---------------- menu ----------------

def vt_enable():
    for handle in (-11, -12):  # stdout, stderr
        try:
            h = ctypes.windll.kernel32.GetStdHandle(handle)
            mode = ctypes.c_uint32()
            if ctypes.windll.kernel32.GetConsoleMode(h, ctypes.byref(mode)):
                ctypes.windll.kernel32.SetConsoleMode(h, mode.value | 0x0004)
        except Exception:
            pass


def get_key():
    c = msvcrt.getwch()
    if c in ("\xe0", "\x00"):
        return {"H": "up", "P": "down", "K": "left", "M": "right"}.get(msvcrt.getwch(), "")
    return {"w": "up", "W": "up", "s": "down", "S": "down", "\r": "enter", "\x1b": "esc"}.get(c, "")


def window_size():
    try:
        class SCR(ctypes.Structure):
            _fields_ = [("szSize", ctypes.c_short * 2), ("szCursorPosition", ctypes.c_short * 2),
                        ("wAttributes", ctypes.c_ushort), ("srWindow", ctypes.c_short * 4),
                        ("dwMaximumWindowSize", ctypes.c_short * 2)]
        h = ctypes.windll.kernel32.GetStdHandle(-11)
        buf = SCR()
        if ctypes.windll.kernel32.GetConsoleScreenBufferInfo(h, ctypes.byref(buf)):
            return buf.srWindow[2] - buf.srWindow[0] + 1, buf.srWindow[3] - buf.srWindow[1] + 1
    except Exception:
        pass
    try:
        import shutil
        return os.get_terminal_size().columns, os.get_terminal_size().lines
    except Exception:
        return 80, 24


def cursor_row():
    """Query terminal cursor row via DSR; returns None if unsupported."""
    sys.stdout.write("\x1b[6n")
    sys.stdout.flush()
    buf = ""
    end = time.time() + 0.5
    while time.time() < end:
        if msvcrt.kbhit():
            c = msvcrt.getwch()
            buf += c
            if c == "R":
                break
        time.sleep(0.005)
    m = re.match(r"\x1b\[(\d+);(\d+)R", buf)
    return int(m.group(1)) if m else None


def show_menu(items, default=0, title=None):
    """Fixed list, arrows/W/S move, Enter select, Esc cancel. Selected row highlighted.
    Renders the whole block (optional title + items) at a queried absolute row on
    every keypress, so a mangled row can never corrupt the next frame."""
    n = len(items)
    if n == 0:
        return -1
    sel = max(0, min(default, n - 1))
    width, height = window_size()
    w = max(20, width - 2)
    clip = lambda s: s if len(s) <= w else s[:w]
    block = n + (1 if title else 0)

    def line(i, cur):
        return clip((" > " if i == cur else "   ") + items[i])

    top = cursor_row()
    overflow = anchored = False
    if top is not None:
        if top + block > height:
            delta = top + block - height + 1
            sys.stdout.write(f"\x1b[{delta}S")
            sys.stdout.flush()
            overflow = True
        else:
            anchored = True
    if not anchored:
        top = max(1, height - block - 1)
        park = height - 1
    else:
        park = top + block

    def render(cur):
        if title:
            sys.stdout.write(f"\x1b[{top};1H\x1b[2K{clip(title)}")
        for i in range(n):
            styled = f"\x1b[7m{line(i, cur)}\x1b[0m" if i == cur else line(i, cur)
            sys.stdout.write(f"\x1b[{top + (1 if title else 0) + i};1H\x1b[2K{styled}")
        sys.stdout.write(f"\x1b[{park};1H")
        sys.stdout.flush()

    render(sel)
    while True:
        k = get_key()
        if k == "up":
            sel = (sel - 1) % n
            render(sel)
        elif k == "down":
            sel = (sel + 1) % n
            render(sel)
        elif k == "enter":
            break
        elif k == "esc":
            sel = -1
            break
    sys.stdout.flush()
    return sel


def provider_menu():
    state = load_state()
    default = state.get("provider", "official") if state.get("provider") in PROVIDERS else "official"
    names = sorted(PROVIDERS) + ["quit"]
    idx = show_menu(names, names.index(default))
    if idx < 0 or names[idx] == "quit":
        return None
    return names[idx]


def model_menu(name):
    state = load_state()
    default = state.get("model", "") if state.get("provider") == name else ""
    if name == "minimax":
        try:
            got = input("Model [default: MiniMax-M2.7]: ")
        except EOFError:
            got = ""
        return got or "MiniMax-M2.7"
    if name == "openrouter":
        print("Codex and free models first (any model ID can be typed):", file=sys.stderr)
    models = get_models(name)
    if name == "openrouter":
        models = ([m for m in models if "codex" in m["id"]]
                  + [m for m in models if ":free" in m["id"] and "codex" not in m["id"]]
                  + [m for m in models if ":free" not in m["id"] and "codex" not in m["id"]][:15])
    items = [m["id"] + (f"  ({m['context']:,} context)" if type(m.get("context")) is int and m["context"] > 0 else "") for m in models]
    defIdx = next((i for i, m in enumerate(models) if m["id"] == default), 0)
    idx = show_menu(items, defIdx, title=f"Available {name} models:")
    if idx < 0:
        try:
            got = input(f"Type model id [default: {default or models[0]['id']}]: ")
        except EOFError:
            got = ""
        return got or (default or models[0]["id"])
    return models[idx]["id"]


# ---------------- apply ----------------

def write_apply(env, delete, run_claude, claude_args=()):
    with open(APPLY_FILE, "w", encoding="utf-8") as f:
        for k in delete:
            f.write(f'Remove-Item "Env:{k}" -ErrorAction SilentlyContinue\n')
        for k, v in env.items():
            f.write(f"Set-Item 'Env:{k}' '{str(v).replace(chr(39), chr(39) + chr(39))}'\n")
        if run_claude:
            f.write('Write-Host "Starting Claude Code (env vars inherited from this session)..." -ForegroundColor Gray\n')
            f.write('Remove-Item -LiteralPath $PSCommandPath -ErrorAction SilentlyContinue\n')
            command = "claude --model $env:ANTHROPIC_MODEL" if env.get("ANTHROPIC_MODEL") else "claude"
            for arg in claude_args:
                command += " '" + arg.replace("'", "''") + "'"
            f.write(command + "\n")


def apply(name, model_id=None, no_run=False, claude_args=()):
    p = PROVIDERS[name]
    if name != "official" and (not isinstance(model_id, str) or not model_id.strip()):
        die("A nonempty model ID is required.")
    if name == "commandcode" and not model_id.startswith("claude-"):
        die("Command Code's Messages endpoint only serves Claude models. For Codex models use cc openrouter openai/gpt-5.3-codex.")
    delete, env = [], {}
    if name == "official":
        delete = CC_VARS + OAI_VARS
        run_claude = not no_run
    elif p["format"] == "anthropic":
        # Gateways choose either bearer or x-api-key authentication.
        delete = list(OAI_VARS) + ["ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY"]
        token = ""
        if p.get("keyEnv"):
            token = os.environ.get(p["keyEnv"]) or user_env(p["keyEnv"])
            if not token:
                die(f"{p['keyEnv']} is not set (it lives in your PowerShell profile).")
        if p.get("key"):
            key = auth_key(p["key"])
            if not key:
                die(f"No key for {name}. Use commandcode auth login or CMD_API_KEY for Command Code; otherwise configure this provider in opencode or its API_KEY environment variable.")
            token = key
        if name in ("opencode-go", "opencode-zen"):
            known = [m["id"] for m in get_models(name)]
            if known and model_id not in known:
                warn(f"'{model_id}' not in live gateway model list - CC may reject it.")
        env["ANTHROPIC_BASE_URL"] = p["base"]
        if p.get("auth") == "bearer":
            env["ANTHROPIC_AUTH_TOKEN"] = token
            env["ANTHROPIC_API_KEY"] = ""
        else:
            env["ANTHROPIC_API_KEY"] = token
        # ANTHROPIC_CUSTOM_HEADERS breaks auth: CC then sends both Authorization
        # and x-api-key; the gateway 401s ("Invalid API key").
        delete.extend(["ANTHROPIC_CUSTOM_HEADERS", "ENABLE_TOOL_SEARCH", "CLAUDE_CODE_AUTO_MODE_SERVER"])
        for v in ["ANTHROPIC_MODEL", "ANTHROPIC_DEFAULT_OPUS_MODEL", "ANTHROPIC_DEFAULT_SONNET_MODEL",
                  "ANTHROPIC_DEFAULT_HAIKU_MODEL", "ANTHROPIC_DEFAULT_FABLE_MODEL", "CLAUDE_CODE_SUBAGENT_MODEL"]:
            env[v] = model_id
        if name == "opencode-zen":
            warn("opencode-zen unverified - the account key 401s on the zen gateway; "
                 "you may need a dedicated zen API key from https://opencode.ai/zen")
        if name == "openrouter" and not model_id.startswith("anthropic/"):
            warn("Non-Claude models are experimental in Claude Code; usage is billed to OpenRouter, not your ChatGPT subscription.")
        run_claude = not no_run
    else:
        delete = list(CC_VARS) + list(OAI_VARS)
        key = "" if p["key"] == "oauth" else auth_key(p["key"])
        if name == "openai-oauth":
            ensure_oauth_proxy()
        elif not key:
            die(f"No API key configured for {name}.")
        ensure_anthropic_proxy()
        env["ANTHROPIC_BASE_URL"] = f"http://127.0.0.1:{ADAPTER_PORT}/{name}"
        env["ANTHROPIC_API_KEY"] = key or "openai-oauth"
        env["ENABLE_TOOL_SEARCH"] = "false"
        # OpenAI-format providers cannot perform Anthropic's server-side safety checks.
        # Keep Claude's existing client-side classifier and avoid the billing notice.
        env["CLAUDE_CODE_AUTO_MODE_SERVER"] = "0"
        for v in CC_VARS:
            if v == "ANTHROPIC_MODEL" or v.startswith("ANTHROPIC_DEFAULT_") or v == "CLAUDE_CODE_SUBAGENT_MODEL":
                env[v] = model_id
        run_claude = not no_run

    if "CLAUDE_CODE_MAX_CONTEXT_TOKENS" not in delete:
        delete.append("CLAUDE_CODE_MAX_CONTEXT_TOKENS")
    if name != "official":
        context = next((m.get("context") for m in get_models(name) if m["id"] == model_id), None)
        if type(context) is int and context > 0:
            env["CLAUDE_CODE_MAX_CONTEXT_TOKENS"] = context

    save_state({"provider": name, "model": model_id or ""})
    write_apply(env, delete, run_claude, claude_args)
    print()
    print("Switched: " + (name if name == "official" else f"{name}/{model_id}"))
    print(f"Effective: ANTHROPIC_BASE_URL={env.get('ANTHROPIC_BASE_URL', '')}  "
          f"model={env.get('ANTHROPIC_MODEL', '')}  OPENAI_BASE_URL={env.get('OPENAI_BASE_URL', '')}  "
          f"context={env.get('CLAUDE_CODE_MAX_CONTEXT_TOKENS', 'Claude default')}")


def main():
    for f in (APPLY_FILE,):
        if os.path.exists(f):
            os.remove(f)
    vt_enable()
    args = sys.argv[1:]
    no_run, pos = False, []
    while args:
        if args[0].lower() in ("-norun", "--norun"):
            no_run = True
            args.pop(0)
        elif args[0].startswith("-") or len(pos) >= (1 if pos and pos[0].lower() == "official" else 2):
            break
        else:
            pos.append(args.pop(0))
    claude_args = args[1:] if args[:1] == ["--"] else args
    if no_run and claude_args:
        die("-NoRun cannot be combined with Claude launch arguments.")
    if not pos and claude_args in (["--help"], ["-h"]):
        print("Usage: cc [provider] [model] [-NoRun] [Claude arguments]")
        print("cc --resume [session-id-or-name] | cc --continue | cc status")
        print("Resume reuses the last selected provider/model; specify both to override.")
        return

    if pos and pos[0] == "status":
        state = load_state()
        print("Current session:")
        print(f"  ANTHROPIC_BASE_URL : {os.environ.get('ANTHROPIC_BASE_URL', '')}")
        print(f"  ANTHROPIC_MODEL    : {os.environ.get('ANTHROPIC_MODEL', '')}")
        print(f"  Context tokens     : {os.environ.get('CLAUDE_CODE_MAX_CONTEXT_TOKENS', 'Claude default')}")
        print(f"  OPENAI_BASE_URL    : {os.environ.get('OPENAI_BASE_URL', '')}")
        if state:
            print(f"  Last chosen         : {state.get('provider')} / {state.get('model')}")
        return

    state = load_state()
    name = None
    if pos:
        name = pos[0].lower()
        if name not in PROVIDERS:
            die(f"Unknown provider '{name}'. Valid: {', '.join(sorted(PROVIDERS))}, status")
    elif claude_args and state.get("provider") in PROVIDERS:
        name = state["provider"]
    else:
        name = provider_menu()
        if not name:
            print("No change.")
            sys.exit(1)

    if name == "official":
        model_id = None
    else:
        model_id = pos[1] if len(pos) > 1 else None
        if not model_id and claude_args and state.get("provider") == name:
            model_id = state.get("model")
        model_id = model_id or model_menu(name)

    apply(name, model_id, no_run, claude_args=claude_args)


if __name__ == "__main__":
    main()
