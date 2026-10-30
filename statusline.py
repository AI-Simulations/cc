"""Claude Code status line: model, effort, context, plan quota or API spend, cache, API time.

Every field is optional so third-party providers that omit data still render.
"""
import colorsys
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
import zlib
from datetime import datetime

DIM, BOLD, RESET = "\033[2m", "\033[1m", "\033[0m"
GREEN, YELLOW, RED, CYAN, MAGENTA = "\033[32m", "\033[33m", "\033[31m", "\033[36m", "\033[35m"
BLUE, ORANGE = "\033[34m", "\033[38;5;208m"
SEP = f" {DIM}│{RESET} "


def k(n):
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}".rstrip("0").rstrip(".") + "M"
    return f"{n / 1000:.0f}k" if n >= 1000 else str(n)


def rgb(hue, sat=0.6, val=0.9):
    r, g, b = (round(c * 255) for c in colorsys.hsv_to_rgb(hue / 360, sat, val))
    return f"\033[38;2;{r};{g};{b}m"


def color(pct):
    # Smooth green (0%) -> yellow -> red (100%).
    return rgb(120 * (1 - min(max(pct, 0), 100) / 100))


def bar(pct, width=8):
    # Each cell takes the gradient color of its own position, so the bar shows how far into the red it reaches.
    filled = round(min(max(pct, 0), 100) * width / 100)
    cells = "".join(f"{color((i + 0.5) * 100 / width)}▰" for i in range(filled))
    return f"{cells}{DIM}{'▱' * (width - filled)}{RESET}"


def pct_text(pct):
    return f"{color(pct)}{BOLD if pct >= 90 else ''}{pct}%{RESET}"


def span(seconds):
    seconds = max(int(seconds), 0)
    d, h, m = seconds // 86400, seconds % 86400 // 3600, seconds % 3600 // 60
    return f"{d}d{h}h" if d else f"{h}h{m:02d}m" if h else f"{m}m"


def last_usage(path):
    # Fallback when context_window is absent: newest assistant usage in the transcript.
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except (OSError, TypeError):
        return {}
    for line in reversed(lines):
        try:
            usage = json.loads(line).get("message", {}).get("usage")
        except (ValueError, AttributeError):
            continue
        if isinstance(usage, dict):
            return usage
    return {}


STATE_DIR = os.path.expanduser("~/.claude/statusline")
# Reasoning steps, coolest to hottest. The five named levels are the ramp's anchors; the ladder
# a provider publishes for one model (it may add `off` or `ultra`) is scaled onto that ramp.
EFFORT_HUES = {"low": 200, "medium": 140, "high": 50, "xhigh": 25, "max": 350}  # cool -> hot
EFFORT_ORDER = ["off", "low", "medium", "high", "xhigh", "max", "ultra"]
EFFORT_RAMP = [EFFORT_HUES[level] for level in ("low", "medium", "high", "xhigh", "max")]
CLAUDE_HUE = 15  # Anthropic clay
WINDOW_LABELS = {"five_hour": "5h", "seven_day": "7d", "month": "mo", "seven_day_opus": "7d opus", "seven_day_sonnet": "7d sonnet"}
CLAUDE_ALIASES =("claude", "opus", "sonnet", "haiku", "fable", "mythos")


def effort_hue(fraction):
    """0 = the coolest hue, 1 = the hottest, sampled along the low -> max ramp."""
    t = min(max(float(fraction), 0.0), 1.0) * (len(EFFORT_RAMP) - 1)
    i = min(int(t), len(EFFORT_RAMP) - 2)
    step = t - i
    a = colorsys.hsv_to_rgb(EFFORT_RAMP[i] / 360, 1, 1)
    b = colorsys.hsv_to_rgb(EFFORT_RAMP[i + 1] / 360, 1, 1)
    r, g, blue = (a[c] + (b[c] - a[c]) * step for c in range(3))
    return colorsys.rgb_to_hsv(r, g, blue)[0] * 360


def effort_ladder(levels):
    """The model's reasoning steps in order, or the usual Claude ladder when it publishes none."""
    if levels == []:
        return []  # the model takes no adjustable reasoning effort
    if not isinstance(levels, (list, tuple)):
        return list(EFFORT_HUES)  # an unreadable cache must not invent a ladder
    names = [name for name in levels if isinstance(name, str)]
    if not names:
        return list(EFFORT_HUES)
    rank = {name: i for i, name in enumerate(EFFORT_ORDER)}
    return [name for _, name in sorted(enumerate(names), key=lambda pair: (rank.get(pair[1], len(EFFORT_ORDER)), pair[0]))]


def effort_indicator(effort, dim=False, label=True, levels=None):
    """One circle per step up the model's own reasoning ladder, in that ladder's own colour.

    `levels` is the list the provider publishes for this model: None keeps the usual Claude
    ladder, and [] renders `default`, since the model has no adjustable reasoning effort.
    """
    if levels == []:
        return DIM + "default" + RESET
    ladder = effort_ladder(levels)
    if not effort or effort not in ladder:
        level, hue = 0, 280
    else:
        rank = ladder.index(effort)
        level, hue = rank + 1, effort_hue(rank / (len(ladder) - 1) if len(ladder) > 1 else 1.0)
    # An effort off the ladder has no circles: name it rather than show nothing.
    text = (effort + " " if label and level else "") + ("●" * level or effort or "")
    return rgb(hue, val=0.55 if dim else 0.9) + text + RESET


def load_json(name):
    try:
        with open(os.path.join(STATE_DIR, name), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_json(name, data):
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(os.path.join(STATE_DIR, name), "w", encoding="utf-8") as f:
            json.dump(data, f)
    except OSError:
        pass


def read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def default_effort(provider, model_id):
    """The level a profile with no effort runs at (the picker's EFFORT_INFO): cmdc's per-model or global
    config, Codex's default_reasoning_level, Claude Code's own settings; None when unknown."""
    if not model_id:
        return None
    if provider in ("commandcode", "commandcode-cli"):
        cfg = read_json(os.path.expanduser("~/.commandcode/config.json"))
        return (cfg.get("reasoningEffort") or {}).get(model_id) or cfg.get("effort")
    if provider == "openai-oauth":
        rows = read_json(os.path.join(os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex"),
                                      "models_cache.json")).get("models") or ()
        return next((r.get("default_reasoning_level") for r in rows
                     if isinstance(r, dict) and str(r.get("slug", "")).casefold() == str(model_id).casefold()), None)
    if provider == "official":
        cfg = read_json(os.path.expanduser("~/.claude/settings.json"))
        return ((cfg.get("modelSettings") or {}).get(model_id) or {}).get("effortLevel") or cfg.get("effortLevel")
    return None


def effort_levels(provider, model_id):
    """The reasoning efforts the provider publishes for this model; None when it publishes none.

    Local reads only: Codex's model cache for the OpenAI route, the copy cc-switch.py keeps of
    Command Code's catalog for its two. The status line never waits on a network call.
    """
    if not model_id:
        return None
    key = str(model_id).casefold()
    if provider == "openai-oauth":
        rows = read_json(os.path.join(os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex"),
                                      "models_cache.json")).get("models") or ()
        for row in rows:
            if isinstance(row, dict) and str(row.get("slug", "")).casefold() == key:
                levels = row.get("supported_reasoning_levels")
                if not isinstance(levels, list):
                    return None
                return [e["effort"] for e in levels if isinstance(e, dict) and isinstance(e.get("effort"), str)]
        return None
    if provider in ("commandcode", "commandcode-cli"):
        models = read_json(os.path.expanduser("~/.config/cc/efforts.json")).get("models") or {}
        for mid, levels in models.items():
            if str(mid).casefold() == key:
                return levels if isinstance(levels, list) else None
    return None


def claude_json():
    try:
        with open(os.path.expanduser("~/.claude.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def credentials():
    # ~/.claude/.credentials.json carries the claude.ai login; a Console login instead stores
    # the key it created in ~/.claude.json.
    try:
        with open(os.path.expanduser("~/.claude/.credentials.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def subscription_login(creds):
    """True when a claude.ai login is stored, which pays for the session instead of an API key."""
    oauth = creds.get("claudeAiOauth")
    return isinstance(oauth, dict) and bool(oauth.get("accessToken") or oauth.get("refreshToken"))


def api_billing(d, stored, creds):
    """True when the session bills the Anthropic API per token instead of a claude.ai plan.

    ANTHROPIC_API_KEY outranks every login once you approve it (-p always uses it); below that a
    claude.ai login beats a stored Console key. Claude Code sends rate_limits only to subscribers,
    so the field's absence is what separates a keyed session from a planned one.
    """
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key:
        # You approve or decline each key once; a declined key leaves the session on your plan.
        return key[-20:] not in ((stored.get("customApiKeyResponses") or {}).get("rejected") or [])
    if subscription_login(creds):
        return False
    return bool(stored.get("primaryApiKey")) and "rate_limits" not in d


def plan_name(account):
    plan = (account.get("organizationType") or "").removeprefix("claude_").replace("_", " ").title()
    # Tier looks like "default_claude_max_5x"; the multiplier is the only part the plan name lacks.
    mult = re.search(r"_(\d+x)$", account.get("organizationRateLimitTier") or "")
    if plan and mult:
        plan += " " + mult.group(1)
    if plan and account.get("hasExtraUsageEnabled"):
        plan += " +extra"
    return plan or None


def quota_source():
    # cc sets ANTHROPIC_BASE_URL per provider; the status line inherits it.
    url = os.environ.get("ANTHROPIC_BASE_URL", "").lower().rstrip("/")
    # `cc official` passes the claude.ai login through the adapter's /official route
    if not url or "anthropic.com" in url or url.endswith("/official"):
        return "anthropic"
    if "commandcode" in url:  # API route and the cmdc adapter route share one account
        return "commandcode"
    if url.endswith("/openai-oauth"):
        return "openai"
    if "opencode.ai/zen/go" in url or url.endswith("/opencode-go"):  # direct, or via the adapter
        return "opencode-go"
    if "openrouter.ai" in url:
        return "openrouter"
    if "charm.land" in url or url.endswith("/hyper"):
        return "hyper"
    return None


def fetch_json(url, headers):
    req = urllib.request.Request(url, headers={"User-Agent": "cc-statusline", **headers})
    with urllib.request.urlopen(req, timeout=3) as r:
        return json.load(r)


def commandcode_quota():
    key = os.environ.get("CMD_API_KEY")
    if not key:
        with open(os.path.expanduser("~/.commandcode/auth.json"), encoding="utf-8-sig") as f:
            key = json.load(f)["apiKey"]
    d = fetch_json("https://api.commandcode.ai/alpha/billing/credits", {"Authorization": "Bearer " + key})
    windows = d.get("windowLimits") or {}
    limits = {}
    for src, name in (("fiveHour", "five_hour"), ("weekly", "seven_day")):
        w = windows.get(src)
        if isinstance(w, dict) and w.get("cap"):
            limits[name] = {"used_percentage": round(w["used"] * 100 / w["cap"]), "resets_at": w["resetAt"] / 1000}
    credits = d.get("credits") or {}
    left = sum(credits.get(f) or 0 for f in ("monthlyCredits", "purchasedCredits", "freeCredits"))
    return {"plan": "Command Code", "rate_limits": limits, "extra": f"${left:.2f} left"}


def openai_quota():
    home = os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex")
    with open(os.path.join(home, "auth.json"), encoding="utf-8-sig") as f:
        tokens = json.load(f)["tokens"]
    d = fetch_json("https://chatgpt.com/backend-api/wham/usage", {
        "Authorization": "Bearer " + tokens["access_token"], "ChatGPT-Account-Id": tokens.get("account_id") or ""})
    limits = {}
    for w in ((d.get("rate_limit") or {}).get(k) for k in ("primary_window", "secondary_window")):
        if isinstance(w, dict) and w.get("used_percent") is not None:
            secs = w.get("limit_window_seconds") or 0
            name = {18000: "five_hour", 604800: "seven_day"}.get(secs, f"{secs // 3600}h")
            limits[name] = {"used_percentage": w["used_percent"], "resets_at": w.get("reset_at")}
    return {"plan": f"ChatGPT {(d.get('plan_type') or '').title().replace('Prolite', 'Pro Lite')}".strip(), "rate_limits": limits}


def opencode_go_quota():
    # cc hands the Go key to Claude Code as ANTHROPIC_AUTH_TOKEN; a cc subagent on Go while the
    # session is elsewhere reads cc's own source instead. 403 without a Go subscription.
    key = os.environ.get("OPENCODE_GO_API_KEY")
    if not key and quota_source() == "opencode-go":
        key = os.environ["ANTHROPIC_AUTH_TOKEN"]
    if not key:
        with open(os.path.expanduser("~/.local/share/opencode/auth.json"), encoding="utf-8") as f:
            key = json.load(f)["opencode-go"]["key"]
    d = fetch_json("https://opencode.ai/zen/go/v1/usage", {"Authorization": "Bearer " + key})
    limits = {}
    for src, name in (("rolling", "five_hour"), ("weekly", "seven_day"), ("monthly", "month")):
        w = (d.get("usage") or {}).get(src)
        if isinstance(w, dict) and w.get("percent") is not None:
            resets = datetime.fromisoformat(w["resetsAt"].replace("Z", "+00:00")).timestamp() if w.get("resetsAt") else None
            limits[name] = {"used_percentage": round(w["percent"]), "resets_at": resets}
    return {"plan": "OpenCode Go", "rate_limits": limits}


def hyper_quota():
    # Crush keeps Hyper's hourly OAuth token fresh while it runs; an expired one just fails here.
    key = os.environ.get("HYPER_API_KEY")
    if not key:
        with open(os.path.expanduser("~/AppData/Local/crush/crush.json"), encoding="utf-8") as f:
            key = json.load(f)["providers"]["hyper"]["api_key"]
    d = fetch_json("https://hyper.charm.land/api/v1/credits", {"Authorization": "Bearer " + key})
    if d.get("balance") is not None:
        extra = f"◆ {d['balance']:,} hypercredits"
    elif d.get("balance_usd") is not None:
        extra = f"${float(d['balance_usd']):.2f} left"
    else:
        raise ValueError("no balance")
    return {"plan": "Hyper", "rate_limits": {}, "extra": extra}


def openrouter_quota():
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key and quota_source() == "openrouter":
        key = os.environ["ANTHROPIC_AUTH_TOKEN"]
    if not key:
        with open(os.path.expanduser("~/.local/share/opencode/auth.json"), encoding="utf-8") as f:
            key = json.load(f)["openrouter"]["key"]
    d = fetch_json("https://openrouter.ai/api/v1/key", {"Authorization": "Bearer " + key})["data"]
    left = d.get("limit_remaining")
    extra = f"${left:.2f} left" if left is not None else f"${d.get('usage') or 0:.2f} used"
    return {"plan": "OpenRouter", "rate_limits": {}, "extra": extra}


QUOTA_FETCHERS = {"commandcode": commandcode_quota, "openai": openai_quota,
                  "opencode-go": opencode_go_quota, "hyper": hyper_quota, "openrouter": openrouter_quota}
QUOTA_TTL = 10  # the usage endpoints are plain account lookups: no model tokens
QUOTA_RETRY = 60  # after a failed fetch
# cc provider names (~/.config/cc/agents.json) that share a quota source; the rest have no quota API.
CC_QUOTA_SOURCES = {"official": "anthropic", "commandcode": "commandcode", "commandcode-cli": "commandcode",
                    "openai-oauth": "openai", "opencode-go": "opencode-go", "openrouter": "openrouter"}


def cc_agents(session_id=None):
    """This session's subagent profiles, or the shared defaults."""
    try:
        with open(os.path.expanduser("~/.config/cc/agents.json"), encoding="utf-8") as f:
            agents = json.load(f)
    except (OSError, ValueError):
        return {}
    # cc-switch's session_launch: a Claude session not started by cc keys its profiles by session id
    launch = os.environ.get("CC_LAUNCH_ID") or (f"claude-{session_id}" if session_id else "")
    if launch:
        try:
            with open(os.path.expanduser("~/.config/cc/session-agents.json"), encoding="utf-8") as f:
                entry = json.load(f).get(launch) or {}
            if isinstance(entry.get("agents"), dict):
                return entry["agents"]
        except (OSError, ValueError, AttributeError):
            pass
    return agents if isinstance(agents, dict) else {}


def subagent_sources(session_id=None):
    """{quota source: [cc subagent names]} for cc's own-process subagents."""
    agents = cc_agents(session_id)
    found = {}
    for name, a in agents.items() if isinstance(agents, dict) else ():
        source = CC_QUOTA_SOURCES.get((a if isinstance(a, dict) else {}).get("provider", "official"))
        if source:
            found.setdefault(source, []).append(name)
    return found


def cc_profiles(session_id=None):
    return {name: profile for name, profile in cc_agents(session_id).items() if isinstance(profile, dict)}


def cc_orphans(session_id):
    """This session's relay jobs nobody waits on any more (cc-switch's orphan_jobs), with the worker's tokens."""
    if not isinstance(session_id, str) or not session_id:
        return []
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("cc_switch", os.path.expanduser("~/.config/cc/cc-switch.py"))
        cc = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cc)
        return [{**item, "tokens": cc._worker_tokens(cc._worker_path({"session_id": item["session"]})) if item["session"] else ""}
                for item in cc.orphan_jobs(session_id)]
    except Exception:  # a status line never fails over an optional row
        return []


def cc_fallback_jobs(session_id):
    """{subagent name: route} for this session's workers that cc moved onto their fallback (cc-switch's fallback_jobs)."""
    if not isinstance(session_id, str) or not session_id:
        return {}
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("cc_switch", os.path.expanduser("~/.config/cc/cc-switch.py"))
        cc = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cc)
        return cc.fallback_jobs(session_id)
    except Exception:  # a status line never fails over an optional row
        return {}


def cc_running_jobs(session_id):
    """Count of this session's relay workers still running (cc-switch's running_jobs)."""
    if not isinstance(session_id, str) or not session_id:
        return 0
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("cc_switch", os.path.expanduser("~/.config/cc/cc-switch.py"))
        cc = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cc)
        return cc.running_jobs(session_id)
    except Exception:  # a status line never fails over an optional row
        return 0


def cc_routing_enabled():
    hooks = (user_settings().get("hooks") or {}).get("PreToolUse") or []
    return any("cc-switch.py" in hook.get("command", "") and "hook-agent" in hook.get("command", "")
               for group in hooks if isinstance(group, dict)
               for hook in group.get("hooks") or [] if isinstance(hook, dict))


def cc_main_provider(source):
    if os.environ.get("CC_MAIN_PROVIDER"):
        return os.environ["CC_MAIN_PROVIDER"]
    url = os.environ.get("ANTHROPIC_BASE_URL", "").lower().rstrip("/")
    for provider in ("commandcode-cli", "commandcode", "nvidia", "minimax", "openrouter", "opencode-zen", "gpt4free", "gemini", "ollama-cloud", "lmstudio"):
        if provider in url:
            return provider
    return {"anthropic": "official", "openai": "openai-oauth", "opencode-go": "opencode-go"}.get(source)


def provider_quota(source, now):
    """Returns (quota, stale_since), refreshing it in the background so no render waits on the network."""
    name = f"quota-{source}.json"
    saved = load_json(name)
    if not saved.get("at"):
        if now - saved.get("tried", 0) >= QUOTA_RETRY:
            return fetch_quota(source, now)
    elif (now - saved["at"] >= QUOTA_TTL and now - saved.get("tried", 0) >= QUOTA_RETRY
          and now - saved.get("claimed", 0) >= QUOTA_TTL):
        save_json(name, {**saved, "claimed": now})  # other sessions' renders leave this refresh to us
        try:
            subprocess.Popen([sys.executable, "-B", os.path.abspath(__file__), "--quota", source], stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except OSError:
            pass
    return saved, saved.get("at") if saved.get("tried", 0) > saved.get("at", 0) else None


def fetch_quota(source, now):
    """Reads and saves the quota; a failed fetch keeps the last good result."""
    name = f"quota-{source}.json"
    saved = load_json(name)
    try:
        q = {**QUOTA_FETCHERS[source](), "at": now}
    except Exception as e:
        # Keep why it failed: an expired key otherwise just makes the bars vanish.
        body = e.read().decode("utf-8", "replace") if isinstance(e, urllib.error.HTTPError) else ""
        error = ("key expired" if "api_key_expired" in body else f"auth failed (HTTP {e.code})" if getattr(e, "code", None) in (401, 403)
                 else "no credentials" if isinstance(e, (OSError, KeyError)) and not isinstance(e, urllib.error.URLError) else "usage unavailable")
        save_json(name, {**saved, "tried": now, "error": error})  # don't retry (and block 3s) every render
        return {**saved, "error": error}, saved.get("at")
    save_json(name, q)
    return q, None


def is_third_party(model_id):
    return bool(model_id) and not model_id.lower().startswith(CLAUDE_ALIASES)



def user_settings():
    try:
        with open(os.path.expanduser("~/.claude/settings.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def settings_effort(model_id):
    s = user_settings()
    return (s.get("modelSettings", {}).get(model_id) or {}).get("effortLevel") or s.get("effortLevel")


def settings_ultracode():
    return user_settings().get("ultracode") is True


def last_prompt(path, tail=262_144):
    # Newest typed user prompt (tool results also arrive as "user" lines, as lists without plain text).
    try:
        with open(path, "rb") as f:
            f.seek(max(0, os.path.getsize(path) - tail))
            lines = f.read().decode("utf-8", "replace").splitlines()
    except (OSError, TypeError):
        return ""
    for line in reversed(lines):
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        # Only prompts you typed; skips task notifications, skill expansions and tool results.
        if entry.get("type") != "user" or (entry.get("origin") or {}).get("kind") != "human":
            continue
        content = (entry.get("message") or {}).get("content")
        if isinstance(content, list):
            content = " ".join(c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text")
        if isinstance(content, str) and content.strip():
            return content
    return ""


def keyword_ultracode(path):
    # Typing "ultracode" in a prompt turns it on for that turn only; the payload doesn't report it.
    return re.search(r"\bultracode\b", last_prompt(path), re.I) is not None


def quota_parts(plan, extra, limits, cached_at, now):
    parts = []
    if plan and (limits or extra):
        parts.append(f"{BOLD}{plan.replace('ChatGPT Prolite', 'ChatGPT Pro Lite')}{RESET}" + (f" {DIM}{extra}{RESET}" if extra else ""))
    for key, lim in (limits if isinstance(limits, dict) else {}).items():
        if not isinstance(lim, dict) or lim.get("used_percentage") is None:
            continue
        label = WINDOW_LABELS.get(key, key.replace("_", " "))
        pct, resets = lim["used_percentage"], lim.get("resets_at")
        if cached_at and resets and resets < now:
            pct, resets = 0, None  # window rolled over since the cache was written
        # A window replayed from the cache is the last figure Claude Code sent, not a live one.
        seg = f"{'~' if cached_at else ''}{label} {bar(pct, 6)} {pct_text(pct)}"
        if resets:
            seg += f" {DIM}↻ {span(resets - now)}{RESET}"
        parts.append(seg)
    return parts


def render(d):
    model = d.get("model") or {}
    effort = d.get("effort")
    # Ultracode: payload flag (top level or under effort), effort level "ultracode", or the settings key.
    ultra = d.get("ultracode") is True or (isinstance(effort, dict) and effort.get("ultracode") is True)
    if isinstance(effort, dict):
        effort = effort.get("level")
    effort = effort or settings_effort(model.get("id"))
    ultra = ultra or effort == "ultracode" or settings_ultracode() or keyword_ultracode(d.get("transcript_path"))
    if effort == "ultracode":
        effort = None
    # /model's Model tab switched this cc session (cc route): Claude Code still reports the launch model.
    launch = os.environ.get("CC_LAUNCH_ID")
    route = (load_json(os.path.expanduser("~/.config/cc/routes.json")) if launch else {}).get(launch)
    if route:
        model = {"id": route["model"], "display_name": route["model"].rsplit("/", 1)[-1]}
        effort = route.get("effort") or effort
    mid = model.get("id") or ""
    # Claude keeps Anthropic's clay; other vendors get a stable hue from their name.
    hue = CLAUDE_HUE if not is_third_party(mid) else zlib.crc32(mid.split("/")[0].encode()) % 360
    head = f"{rgb(hue, 0.45)}{BOLD}{model.get('display_name') or mid or '?'}{RESET}"
    if effort:
        # One circle per step up the model's own reasoning ladder, cool for low, hot for its top.
        head += " " + effort_indicator(effort, label=False, levels=effort_levels(cc_main_provider(None), mid))
    if ultra:
        # Rainbow letters; the start hue drifts with the clock so each refresh shimmers.
        word, start = "ULTRACODE", time.time() * 40
        head += " " + BOLD + "".join(f"{rgb(start + i * 360 / len(word), 0.55, 1.0)}{ch}" for i, ch in enumerate(word)) + RESET
    parts = [head]

    cw = d.get("context_window") or {}
    usage = cw.get("current_usage") or last_usage(d.get("transcript_path"))
    ctx = sum(usage.get(f) or 0 for f in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
    size = cw.get("context_window_size") or 200_000
    if ctx:
        pct = cw.get("used_percentage")
        pct = ctx * 100 // size if pct is None else pct
        parts.append(f"ctx {bar(pct)} {pct_text(pct)} {DIM}{k(ctx)}/{k(size)}{RESET}")

    now = time.time()
    stored, creds = claude_json(), credentials()
    source, extra, on_api = quota_source(), None, api_billing(d, stored, creds)
    if source == "anthropic":
        # Claude's quota arrives with the first reply; replay the cache until then. An API key
        # bills the session per token, so neither the payload nor a cached copy describes it.
        limits, cached_at = ({} if on_api else d.get("rate_limits")), None
        limits = limits if isinstance(limits, dict) else {}
        account = stored.get("oauthAccount") or {}
        uuid = account.get("accountUuid")
        if limits:
            save_json("quota.json", {"rate_limits": limits, "at": now, "account": uuid})
        elif not on_api:
            saved = load_json("quota.json")
            limits, cached_at = saved.get("rate_limits"), saved.get("at")
            # A cache written under another login is that account's quota, not this one's.
            if saved.get("account") != uuid or not isinstance(limits, dict) or not isinstance(cached_at, (int, float)):
                limits, cached_at = {}, None
        plan = None if on_api else plan_name(account)
    elif source:
        q, cached_at = provider_quota(source, now)
        limits, plan, extra = q.get("rate_limits") or {}, q.get("plan"), q.get("extra")
    else:
        # Adapter routes: the x-ratelimit-* windows cc-anthropic-proxy saw on the last reply.
        q = load_json(f"ratelimit-{cc_main_provider(None)}.json")
        limits, cached_at, plan = q.get("rate_limits") or {}, q.get("at"), None
    shown = quota_parts(plan, extra, limits, cached_at, now)
    parts += shown or ([f"{YELLOW}{q['error']}{RESET}"] if source and source != "anthropic" and q.get("error") else [])
    # The session's fallback route (cc fallback); yellow while it served within the last 5 minutes.
    fallback = (load_json(os.path.expanduser("~/.config/cc/fallbacks.json")) if launch else {}).get(launch)
    if fallback:
        served = load_json(f"fallback-{launch}.json").get("at") or 0
        label = f"fallback {fallback['provider']}/{fallback['model']}"
        parts.append(f"{YELLOW}↪ {label} active{RESET}" if now - served < 300 else f"{DIM}↪ {label}{RESET}")
    # The Model tab's provider, beside the model it put in the head.
    if route:
        parts.append(f"{CYAN}via {route['provider']}{RESET}")

    # Configured targets are visible even on official Claude and even on the same provider.
    # Live worker rows separately identify native workers and the real cc relay/worker route.
    sub_lines = []
    child = os.environ.get("CC_AGENT_CHILD") == "1"
    configured = not child and cc_routing_enabled()
    if configured:
        main_provider = cc_main_provider(source)
        # a continued session keeps writing the transcript its workers name as their parent, under a new session id
        moved = {**cc_fallback_jobs(os.path.splitext(os.path.basename(d.get("transcript_path") or ""))[0]),
                 **cc_fallback_jobs(d.get("session_id"))}
        # shortcut: jobs don't record their agent name, so any running worker lights every configured row
        active = bool(cc_running_jobs(os.path.splitext(os.path.basename(d.get("transcript_path") or ""))[0])
                      or cc_running_jobs(d.get("session_id")))
        for name, profile in cc_profiles(d.get("session_id")).items():
            # a worker that cc reran on its fallback shows the route it really runs on, not the configured one
            profile = {**profile, **{k: v for k, v in moved.get(name, {}).items() if k in ("provider", "model", "effort")}}
            provider = profile.get("provider", "official")
            mid = profile.get('model') or 'Claude default'
            target = re.sub(r'^(?:gpt|claude)-', '', mid.rsplit('/', 1)[-1], flags=re.I)
            target = re.sub(r'^(opus|sonnet|haiku)-(\d+)-(\d+)', r'\1 \2.\3', target).replace('-', ' ')
            labels = {'openai-oauth': 'OAuth', 'official': 'Claude', 'nvidia': 'NIM',
                      'commandcode': 'CmdC', 'commandcode-cli': 'CmdC CLI', 'minimax': 'MiniMax',
                      'opencode-go': 'Go', 'opencode-zen': 'Zen', 'openrouter': 'OpenRouter', 'gpt4free': 'g4f',
                      'gemini': 'Gemini', 'ollama-cloud': 'Ollama', 'lmstudio': 'LM Studio'}
            route = 'same provider' if provider == main_provider else labels.get(provider, provider)
            effort = profile.get('effort') or default_effort(provider, profile.get('model'))
            dots = (effort_indicator(effort, dim=not active, label=False, levels=effort_levels(provider, mid))
                    if effort else DIM + 'default' + RESET)
            hue = CLAUDE_HUE if not is_third_party(mid) else zlib.crc32(mid.split('/')[0].encode()) % 360
            head = f"↳ {name}:" if active else f"{DIM}↳ {name}:{RESET}"
            seg = [f"{head} {rgb(hue, 0.45, 0.9 if active else 0.6)}{target}{RESET} {dots} · {route}"
                   + (f" {YELLOW}↪ fallback active{RESET}" if name in moved else "")]
            sub_source = CC_QUOTA_SOURCES.get(provider)
            if sub_source and sub_source != source:
                if sub_source == 'anthropic':
                    saved = load_json('quota.json')
                    account = stored.get('oauthAccount') or {}
                    q, sub_cached = {}, None
                    if saved.get('account') == account.get('accountUuid') and isinstance(saved.get('at'), (int, float)):
                        q, sub_cached = {'plan': plan_name(account), 'rate_limits': saved.get('rate_limits')}, saved['at']
                else:
                    q, sub_cached = provider_quota(sub_source, now)
                plan = (q.get('plan') or '').removeprefix('ChatGPT ')
                shown = quota_parts(plan, q.get('extra'), q.get('rate_limits') or {}, sub_cached, now)
                seg += shown or ([f"{YELLOW}{q['error']}{RESET}"] if q.get('error') else [])
            elif not sub_source and provider != main_provider:
                # Adapter routes: the x-ratelimit-* windows cc-anthropic-proxy saw on its last reply.
                q = load_json(f"ratelimit-{provider}.json")
                seg += quota_parts(None, None, q.get('rate_limits') or {}, q.get('at'), now)
            sub_lines.append(SEP.join(seg))
    for sub_source, names in ({} if configured or child or source == "anthropic" else subagent_sources(d.get("session_id"))).items():
        if sub_source == source:
            continue  # same account as the session, already shown
        if sub_source == "anthropic":
            # Only a Claude session receives this quota; replay its last copy for this login.
            saved = load_json("quota.json")
            account = stored.get("oauthAccount") or {}
            if saved.get("account") != account.get("accountUuid") or not isinstance(saved.get("at"), (int, float)):
                continue
            q, sub_cached = {"plan": plan_name(account), "rate_limits": saved.get("rate_limits")}, saved["at"]
        else:
            q, sub_cached = provider_quota(sub_source, now)
        seg = quota_parts(q.get("plan"), q.get("extra"), q.get("rate_limits") or {}, sub_cached, now)
        if seg:
            sub_lines.append(f"{DIM}↳ subagent {', '.join(names)}{RESET}" + SEP + SEP.join(seg))

    for item in [] if child else cc_orphans(d.get("session_id")):
        state = (f"{BOLD}result unread{RESET} · reaches Claude with your next message" if item["done"]
                 else "running, relay gone")
        tokens = f" · {DIM}{item['tokens']}{RESET}" if item.get("tokens") else ""
        sub_lines.append(f"{DIM}↳ worker {item['job'][:8]}:{RESET} {state} · {item.get('task') or ''}{tokens}")

    cost = d.get("cost") or {}
    if is_third_party(model.get("id")):
        pass  # Claude Code prices unknown models at Opus rates, so its cost is fiction here.
    elif on_api or ("rate_limits" not in d and not subscription_login(creds) and cost.get("total_cost_usd")):
        # Session spend on an API key, at list price; cents until it passes a dollar. A keyed
        # session shows it from the first render, so an untouched session reads $0.000.
        spent = cost.get("total_cost_usd") or 0
        money = f"${spent:,.2f}" if spent >= 1 else f"${spent:.3f}"
        parts.append(f"api {YELLOW}{money}{RESET}")

    cache = d.get("prompt_cache") or {}
    # Warm: reply within the countdown to reuse cached context (fast, cheap).
    # Cold: next reply re-processes the whole context. settings.json refreshInterval keeps it ticking.
    expires = cache.get("expires_at")
    if is_third_party(mid):
        pass  # no cache countdown from other providers; keep the line short like Claude's
    elif cache.get("warm") and isinstance(expires, (int, float)) and expires > now:
        left = expires - now
        ttl = 300 if cache.get("ttl") == "5m" else 3600
        used = round(100 - left * 100 / ttl)
        parts.append(f"cache {color(used)}{BOLD if used >= 90 else ''}{span(left)}{RESET}")
    elif cache.get("warm") is not None:
        parts.append(f"cache {color(100)}off{RESET}")
    return "\n".join([SEP.join(parts), *sub_lines])


if __name__ == "__main__":
    if sys.argv[1:2] == ["--quota"]:
        fetch_quota(sys.argv[2], time.time())
        sys.exit()
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        data = {}
    try:
        line = render(data if isinstance(data, dict) else {})
    except Exception:  # a broken status line is worse than a bare one
        line = str((data.get("model") or {}).get("id") if isinstance(data, dict) and isinstance(data.get("model"), dict) else "?")
    print(line)
