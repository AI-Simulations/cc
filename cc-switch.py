#!/usr/bin/env python3
# cc-switch.py - Claude Code provider switcher (menu UI, stdlib only)
# Usage:
#   cc                     interactive menu, then opens Claude Code
#   cc <provider> [model]  e.g. cc official | cc minimax | cc opencode-go
#   cc -NoRun <provider>   switch without launching a client
#   cc status              show current session state
# Only affects the current PowerShell session: env vars are applied by the
# 'cc' PS function dot-sourcing apply.ps1 (a child process cannot set them).

import json, os, re, subprocess, sys, time, urllib.request, winreg
from pathlib import Path

sys.path.insert(0, os.environ.get("AI_SUITE_DIR") or str(Path(__file__).resolve().parents[1] / "ai-suite"))
from ai_suite.service import background_process_options, ensure_openai_oauth_proxy
from ai_suite import providers as catalog  # book writer's menus, model facts and colors

CFG_DIR = os.path.expanduser(r"~\.config\cc")
APPLY_FILE = os.path.join(CFG_DIR, "apply.ps1")
STATE_FILE = os.path.join(CFG_DIR, "state.json")
OPENCODE_AUTH = os.path.expanduser(r"~\.local\share\opencode\auth.json")
OPENCODE_CONFIG = os.path.expanduser(r"~\.config\opencode\opencode.jsonc")
OAUTH_PORT = 10531
ADAPTER_PORT = 10532
G4F_PORT = 1337

CC_VARS = ["ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY",
           "ANTHROPIC_MODEL", "ANTHROPIC_DEFAULT_OPUS_MODEL", "ANTHROPIC_DEFAULT_SONNET_MODEL",
           "ANTHROPIC_DEFAULT_HAIKU_MODEL", "ANTHROPIC_DEFAULT_FABLE_MODEL", "CLAUDE_CODE_SUBAGENT_MODEL", "ANTHROPIC_CUSTOM_HEADERS", "ENABLE_TOOL_SEARCH", "CLAUDE_CODE_MAX_CONTEXT_TOKENS", "CLAUDE_CODE_AUTO_MODE_SERVER", "CLAUDE_CODE_ALWAYS_ENABLE_EFFORT",
           "ANTHROPIC_DEFAULT_OPUS_MODEL_NAME", "ANTHROPIC_DEFAULT_SONNET_MODEL_NAME", "ANTHROPIC_DEFAULT_HAIKU_MODEL_NAME",
           "ANTHROPIC_DEFAULT_FABLE_MODEL_NAME", "ANTHROPIC_CUSTOM_MODEL_OPTION", "ANTHROPIC_CUSTOM_MODEL_OPTION_NAME", "ENABLE_CLAUDEAI_MCP_SERVERS", "API_TIMEOUT_MS"]
# /model picker entries beyond the chosen model (which also takes Opus and Haiku, so
# background work stays on it), filled with this provider's other models.
PICKER_SLOTS = ["ANTHROPIC_DEFAULT_SONNET_MODEL", "ANTHROPIC_DEFAULT_FABLE_MODEL", "ANTHROPIC_CUSTOM_MODEL_OPTION"]
# Claude Code's own /effort levels (claude --help).
EFFORTS = ["low", "medium", "high", "xhigh", "max"]
OAI_VARS = ["OPENAI_BASE_URL", "OPENAI_API_KEY"]

PROVIDERS = {
    "commandcode": {"label": "Command Code API (needs Provider plan; Go plan is CLI-only)", "format": "anthropic", "base": "https://api.commandcode.ai/provider", "key": "commandcode"},
    "commandcode-cli": {"label": "Command Code subscription via cmdc CLI (Go plan; text tool protocol)", "format": "openai", "key": "cli"},
    "official":     {"label": "Claude official (claude.ai login)", "format": "anthropic", "base": ""},
    "minimax":      {"label": "MiniMax (Anthropic API)", "format": "anthropic", "base": "https://api.minimax.io/anthropic", "keyEnv": "MINIMAX_API_KEY"},
    "opencode-go":  {"label": "OpenCode Go subscription (x-api-key, live key)", "format": "anthropic", "base": "https://opencode.ai/zen/go", "key": "opencode-go", "header": "x-api-key"},
    "opencode-zen": {"label": "OpenCode Zen PAYG (needs account funds; free models are OpenCode-only)", "format": "anthropic", "base": "https://opencode.ai/zen", "key": "opencode", "header": "x-api-key"},
    "openai-oauth": {"label": "OpenAI OAuth proxy - ChatGPT account (OpenAI-format)", "format": "openai", "base": f"http://127.0.0.1:{OAUTH_PORT}/v1", "key": "oauth"},
    "openrouter":   {"label": "Claude Code via OpenRouter (API credits)", "format": "anthropic", "base": "https://openrouter.ai/api", "key": "openrouter", "auth": "bearer"},
    "nvidia":       {"label": "Claude Code via NVIDIA NIM", "format": "openai", "base": "https://integrate.api.nvidia.com/v1", "key": "nvidia"},
    "gpt4free":     {"label": "gpt4free local API (free, best-effort; starts g4f on :1337)", "format": "openai", "base": f"http://127.0.0.1:{G4F_PORT}/v1", "key": "gpt4free"},
}

# go and zen are DIFFERENT gateways with different model catalogs
FALLBACK_MODELS = {
    "commandcode": [("claude-opus-5-5", ""), ("claude-fable-5-1", ""), ("claude-sonnet-5", ""),
                    ("gpt-6-sol", ""), ("deepseek/deepseek-v4.1-flash", "")],
    "opencode-go":  [("glm-5.3", "GLM-5.3"), ("kimi-k3", "Kimi K3"), ("deepseek-v4.1-flash", "DeepSeek V4.1 Flash"),
                     ("deepseek-v4-pro", "DeepSeek V4 Pro"), ("qwen3.8-max", "Qwen3.8 Max"),
                     ("minimax-m3", "MiniMax M3"), ("gpt-6-luna", "GPT-6 Luna")],
    "opencode-zen": [("claude-opus-5-5", ""), ("claude-fable-5-1", ""), ("claude-sonnet-5", ""),
                     ("gpt-6-sol", ""), ("deepseek-v4.1-flash", "")],
    "openrouter":   [("anthropic/claude-opus-5.5", ""), ("anthropic/claude-fable-5.1", ""),
                     ("openai/gpt-6-sol", ""), ("deepseek/deepseek-v4.1-flash", "")],
    "commandcode-cli": [("deepseek/deepseek-v4.1-flash", ""), ("moonshotai/kimi-k3", ""), ("zai-org/glm-5.3", "")],
    "nvidia":       [("deepseek-ai/deepseek-v4-pro", "")],
    "openai-oauth": [("gpt-6-sol", ""), ("gpt-6-luna", "")],
    "minimax":      [("MiniMax-M3", ""), ("MiniMax-M2.7", "")],
    "gpt4free":     [("deepseek-v4-pro", ""), ("deepseek-v4.1-flash", ""), ("glm-5.3", ""), ("kimi-k3", "")],
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
    env_name = {"commandcode": "CMD_API_KEY", "gpt4free": "G4F_API_KEY"}.get(name) or name.upper().replace("-", "_") + "_API_KEY"
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
        if name == "official":
            models = [{"id": mid, "name": "", "context": None, "output": None} for mid in catalog._provider_models("claude")]
        elif name == "commandcode":
            # Claude ids use /messages; the rest route through the adapter's /chat/completions.
            # Models serving neither (e.g. /responses only) cannot work from Claude Code.
            live = fetch("https://api.commandcode.ai/provider/v1/models", {"Authorization": "Bearer " + auth_key(name)})
            for m in live["data"]:
                if {"/messages", "/chat/completions"} & set(m.get("supported_endpoints", [])):
                    models.append({"id": m["id"], "name": m.get("name", ""), "context": m.get("context_window") or m.get("context_length"), "output": None})
        elif name == "commandcode-cli":
            models = [{"id": mid, "name": "", "context": None, "output": None} for mid in sorted(catalog._live_model_ids("commandcode") or ())]
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
        elif name == "gpt4free":
            ensure_g4f()
            live = fetch(f"http://127.0.0.1:{G4F_PORT}/v1/models", timeout=10)
            for m in live["data"]:
                if m.get("image") or m.get("audio") or m.get("video") or re.search(r"image|tts|embed|flux|dall-e|whisper", m["id"]):
                    continue
                models.append({"id": m["id"], "name": "", "context": None, "output": None})
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


def ensure_oauth_proxy():
    try:
        ensure_openai_oauth_proxy()
    except RuntimeError as exc:
        die(str(exc))


def ensure_g4f():
    # gpt4free's OpenAI-compatible server (pip install -U "g4f[api]"); started hidden on first use.
    url = f"http://127.0.0.1:{G4F_PORT}/v1/models"
    try:
        fetch(url, timeout=2)
        return
    except (OSError, ValueError):
        pass
    try:
        import g4f  # noqa: F401
    except ImportError:
        die('gpt4free is not installed: pip install -U "g4f[api]"')
    subprocess.Popen([sys.executable, "-m", "g4f.cli", "--port", str(G4F_PORT), "--no-gui"],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     **background_process_options())
    for _ in range(120):  # g4f imports its providers on startup; allow ~30 s
        try:
            fetch(url, timeout=1)
            return
        except (OSError, ValueError):
            time.sleep(0.25)
    die(f"gpt4free did not start on port {G4F_PORT}; run 'python -m g4f.cli --port {G4F_PORT}' to see why.")


def stop_port_owner(port):
    """Kill the process listening on 127.0.0.1:<port> and wait for the port to free."""
    out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True).stdout
    for parts in map(str.split, out.splitlines()):
        if len(parts) == 5 and parts[1] == f"127.0.0.1:{port}" and parts[3] == "LISTENING":
            subprocess.run(["taskkill", "/F", "/PID", parts[4]], capture_output=True)
    for _ in range(30):
        try:
            fetch(f"http://127.0.0.1:{port}/health", timeout=0.5)
        except (OSError, ValueError):
            return
        time.sleep(0.1)
    die(f"Could not stop the outdated adapter on port {port}; end it in Task Manager and retry.")


def ensure_anthropic_proxy():
    url = f"http://127.0.0.1:{ADAPTER_PORT}/health"
    expected = {"service": "cc-anthropic-proxy", "version": 9}
    try:
        running = fetch(url, timeout=1)
    except (OSError, ValueError):
        running = None
    if running == expected:
        return
    if isinstance(running, dict) and running.get("service") == expected["service"]:
        # An older adapter from a previous install: replace it. Sessions still on it drop.
        stop_port_owner(ADAPTER_PORT)
    elif running is not None:
        die(f"Port {ADAPTER_PORT} is occupied by another service; free it before retrying.")
    subprocess.Popen([sys.executable, os.path.join(CFG_DIR, "cc-anthropic-proxy.py")],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     **background_process_options())
    for _ in range(40):
        try:
            if fetch(url, timeout=1) == expected:
                return
        except (OSError, ValueError):
            pass
        time.sleep(0.1)
    die(f"Claude Messages adapter did not start on port {ADAPTER_PORT}.")


# ---------------- menu ----------------
# Same arrow menus, colors and model facts as book writer (ai_suite.providers).

def provider_menu():
    state = load_state()
    default = state.get("provider") if state.get("provider") in PROVIDERS else "official"
    names = sorted(PROVIDERS)
    width = max(map(len, names))
    tag = "  " + catalog._color("default", "2")
    picked = catalog._arrow_menu("Provider", [
        (n, f"{n:<{width}}  {PROVIDERS[n]['label']}{tag if n == default else ''}") for n in names], default)
    if picked is None:  # not a console: type it
        try:
            picked = input(f"Provider ({', '.join(names)}) [default: {default}]: ").strip().lower() or default
        except EOFError:
            picked = default
    return picked


def model_menu(name):
    state = load_state()
    with catalog._loading(f"{name} models"):
        models = get_models(name)
        catalog._models_dev()
        catalog._artificial_analysis()
    ids = [m["id"] for m in models]
    if not ids:
        die(f"No {name} models available; check its login or network and retry.")
    catalog._refresh_missing_intelligence(ids)  # new AA scores show on the next menu
    default = state.get("model") if state.get("provider") == name and state.get("model") in ids else ids[0]
    facts = {"official": "claude", "commandcode-cli": "commandcode"}.get(name, name)
    rows = []
    for m in models:
        info = catalog._model_facts(facts, m["id"])
        if type(m.get("context")) is int and m["context"] > 0:  # the provider's own figure wins
            info = {**info, "limit": {**(info.get("limit") or {}), "context": m["context"]}}
        rows.append((m["id"], info))
    paid_by = "; your subscription pays" if name in ("official", "opencode-go", "openai-oauth", "commandcode-cli") else ""
    return catalog._pick_model(PROVIDERS[name]["label"], rows, default, "", paid_by)


def effort_menu():
    """Starting effort for the session ("" = Claude's default); /effort changes it later."""
    return catalog._prompt_effort(EFFORTS, load_state().get("effort", ""))


# ---------------- apply ----------------

def ps_quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def write_apply(env, delete, run_claude, claude_args=(), model=None):
    lines = [f'Remove-Item "Env:{k}" -ErrorAction SilentlyContinue' for k in delete]
    lines += [f"Set-Item 'Env:{k}' {ps_quote(v)}" for k, v in env.items()]
    if run_claude:
        # Scope the provider env vars to this launch, so a later plain `claude` opens default Claude.
        names = list(dict.fromkeys([*delete, *env]))
        saved = "; ".join(f"'{k}' = [Environment]::GetEnvironmentVariable('{k}')" for k in names)
        command = " ".join(["claude", *map(ps_quote, (["--model", model] if model else []) + list(claude_args))])
        lines = [f"$ccSaved = @{{{saved}}}", "try {", *lines,
                 'Write-Host "Starting Claude Code..." -ForegroundColor Gray',
                 "Remove-Item -LiteralPath $PSCommandPath -ErrorAction SilentlyContinue",
                 command,
                 "} finally { foreach ($k in $ccSaved.Keys) { [Environment]::SetEnvironmentVariable($k, $ccSaved[$k]) } }"]
    with open(APPLY_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def picker_models(name, model_id):
    """This provider's recent picks, then its fallback list, for the /model picker."""
    ids = [*load_state().get("recent", {}).get(name, []), *(i for i, _ in FALLBACK_MODELS.get(name, []))]
    # Command Code's Claude and other models live behind different base URLs.
    same_route = lambda m: name != "commandcode" or m.startswith("claude-") == model_id.startswith("claude-")
    return [m for m in dict.fromkeys(ids) if m != model_id and same_route(m)]


def apply(name, model_id=None, no_run=False, claude_args=(), effort=None):
    p = PROVIDERS[name]
    if name != "official" and (not isinstance(model_id, str) or not model_id.strip()):
        die("A nonempty model ID is required.")
    # Command Code serves Claude over /messages and everything else over /chat/completions.
    fmt = "openai" if name == "commandcode" and not model_id.startswith("claude-") else p["format"]
    if name in ("opencode-go", "opencode-zen", "commandcode"):
        # Only typed ids can miss: the menu lists usable models only.
        known = [m["id"] for m in get_models(name)]
        if known and model_id not in known:
            warn(f"'{model_id}' is not in {name}'s usable model list - Claude Code may reject it.")
    env = {}
    if name == "official":
        run_claude = not no_run
    elif fmt == "anthropic":
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
        env["ANTHROPIC_BASE_URL"] = p["base"]
        # A bearer token, unlike ANTHROPIC_API_KEY, avoids Claude's "Both claude.ai and
        # ANTHROPIC_API_KEY set" warning. x-api-key gateways also get the key as a header;
        # Claude then sends exactly one x-api-key, which OpenCode accepts beside the bearer.
        env["ANTHROPIC_AUTH_TOKEN"] = token
        if p.get("header") == "x-api-key":
            env["ANTHROPIC_CUSTOM_HEADERS"] = f"x-api-key: {token}"
        if name == "opencode-zen":
            warn("opencode-zen bills the account balance (402 when empty); free models answer only the OpenCode client.")
        if name == "openrouter" and not model_id.startswith("anthropic/"):
            warn("Non-Claude models are experimental in Claude Code; usage is billed to OpenRouter, not your ChatGPT subscription.")
        run_claude = not no_run
    else:
        key ="" if p["key"] in ("oauth", "cli") else auth_key(p["key"])
        if name == "openai-oauth":
            ensure_oauth_proxy()
        elif name == "commandcode-cli":
            warn("Each request runs cmdc once; tools use a text protocol, so expect slower, less reliable tool calls.")
        elif name == "gpt4free":
            ensure_g4f()  # the key is optional: only needed when g4f runs with --g4f-api-key
        elif not key:
            die(f"No API key configured for {name}.")
        ensure_anthropic_proxy()
        env["ANTHROPIC_BASE_URL"] = f"http://127.0.0.1:{ADAPTER_PORT}/{name}"
        env["ANTHROPIC_AUTH_TOKEN"] = key or name  # the adapter reads bearer or x-api-key
        env["ENABLE_TOOL_SEARCH"] = "false"
        run_claude = not no_run

    if name != "official":
        # Gateways cannot perform Anthropic's server-side safety checks. Keep Claude's
        # existing client-side classifier and avoid the billing notice.
        env["CLAUDE_CODE_AUTO_MODE_SERVER"] = "0"
        # claude.ai connectors never load over a gateway; skip their "disabled" notice.
        env["ENABLE_CLAUDEAI_MCP_SERVERS"] = "false"
        # Slow reasoning models and one cmdc run per request outlast Claude's 10-minute
        # request timeout; match the adapter's own 30-minute CLI limit.
        env["API_TIMEOUT_MS"] = "1800000"
        for v in ["ANTHROPIC_MODEL", "ANTHROPIC_DEFAULT_OPUS_MODEL", "ANTHROPIC_DEFAULT_HAIKU_MODEL", "CLAUDE_CODE_SUBAGENT_MODEL"]:
            env[v] = model_id
        env["ANTHROPIC_DEFAULT_OPUS_MODEL_NAME"] = env["ANTHROPIC_DEFAULT_HAIKU_MODEL_NAME"] = model_id
        # ponytail: CLAUDE_CODE_MAX_CONTEXT_TOKENS stays the chosen model's; a /model pick
        # with a smaller window can overflow before auto-compaction.
        for slot, other in zip(PICKER_SLOTS, picker_models(name, model_id)):
            env[slot] = env[slot + "_NAME"] = other
        # Claude Code keeps /effort off for model ids it does not know; the adapter
        # forwards it as reasoning_effort, Anthropic-format gateways get it as sent.
        env["CLAUDE_CODE_ALWAYS_ENABLE_EFFORT"] = "1"
    if effort and run_claude:
        claude_args = ["--effort", effort, *claude_args]

    if name != "official":
        context = next((m.get("context") for m in get_models(name) if m["id"] == model_id), None)
        if not (type(context) is int and context > 0):  # the menu's models.dev figure
            facts = {"commandcode-cli": "commandcode"}.get(name, name)
            context = (catalog._models_dev_facts(facts, model_id).get("limit") or {}).get("context")
        if type(context) is int and context > 0:
            env["CLAUDE_CODE_MAX_CONTEXT_TOKENS"] = context

    state = load_state()
    recent = state.get("recent", {})
    if model_id:
        recent[name] = list(dict.fromkeys([model_id, *recent.get(name, [])]))[:8]
    save_state({**state, "provider": name, "model": model_id or "", "recent": recent, **({} if effort is None else {"effort": effort})})
    # Clear every switcher variable this route does not set, so no earlier choice leaks in.
    write_apply(env, [v for v in CC_VARS + OAI_VARS if v not in env], run_claude, claude_args, model_id)
    print()
    print("Switched: " + (f"{name}/{model_id}" if model_id else name))
    print(f"Effective: ANTHROPIC_BASE_URL={env.get('ANTHROPIC_BASE_URL', '')}  "
          f"model={model_id or ''}  OPENAI_BASE_URL={env.get('OPENAI_BASE_URL', '')}  "
          f"context={env.get('CLAUDE_CODE_MAX_CONTEXT_TOKENS', 'Claude default')}  "
          f"effort={effort or 'Claude default'} (/effort changes it)")


def main():
    for f in (APPLY_FILE,):
        if os.path.exists(f):
            os.remove(f)
    args = sys.argv[1:]
    no_run, pos = False, []
    while args:
        if args[0].lower() in ("-norun", "--norun"):
            no_run = True
            args.pop(0)
        elif args[0].startswith("-") or len(pos) >= 2:
            break
        else:
            pos.append(args.pop(0))
    claude_args = args[1:] if args[:1] == ["--"] else args
    if no_run and claude_args:
        die("-NoRun cannot be combined with Claude launch arguments.")
    if not pos and claude_args in (["--help"], ["-h"]):
        print("Usage: cc [provider] [model] [-NoRun] [Claude arguments]")
        print("cc --resume [session-id-or-name] | cc --continue | cc status")
        print("Every launch, resume included, asks for the provider and model unless both are given.")
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

    try:
        name = pos[0].lower() if pos else provider_menu()
        if name not in PROVIDERS:
            die(f"Unknown provider '{name}'. Valid: {', '.join(sorted(PROVIDERS))}, status")
        model_id = pos[1] if len(pos) > 1 else model_menu(name)
        # Asked with the menus only; a typed `cc <provider> <model>` passes --effort itself.
        effort = effort_menu() if len(pos) < 2 and not no_run and not any(
            a == "--effort" or a.startswith("--effort=") for a in claude_args) else None
    except KeyboardInterrupt:
        print("No change.")  # a cancel is not a failure: the PowerShell wrapper must not throw
        return

    apply(name, model_id, no_run, claude_args=claude_args, effort=effort)


if __name__ == "__main__":
    main()
