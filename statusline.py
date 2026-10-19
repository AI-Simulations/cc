"""Claude Code status line: model, effort, context, plan quota or API spend, cache, API time.

Every field is optional so third-party providers that omit data still render.
"""
import colorsys
import json
import os
import re
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
EFFORT_HUES = {"low": 200, "medium": 140, "high": 50, "xhigh": 25, "max": 350}  # cool -> hot
CLAUDE_HUE = 15  # Anthropic clay
WINDOW_LABELS = {"five_hour": "5h", "seven_day": "7d", "month": "mo", "seven_day_opus": "7d opus", "seven_day_sonnet": "7d sonnet"}
CLAUDE_ALIASES =("claude", "opus", "sonnet", "haiku", "fable", "mythos")


def effort_indicator(effort, dim=False, label=True):
    level = list(EFFORT_HUES).index(effort) + 1 if effort in EFFORT_HUES else 0
    text = (effort + " " if label else "") + "●" * level
    return rgb(EFFORT_HUES.get(effort, 280), val=0.55 if dim else 0.9) + text + RESET


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
    if not url or "anthropic.com" in url:
        return "anthropic"
    if "commandcode" in url:  # API route and the cmdc adapter route share one account
        return "commandcode"
    if url.endswith("/openai-oauth"):
        return "openai"
    if "opencode.ai/zen/go" in url:
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
QUOTA_TTL = 120
# cc provider names (~/.config/cc/agents.json) that share a quota source; the rest have no quota API.
CC_QUOTA_SOURCES = {"official": "anthropic", "commandcode": "commandcode", "commandcode-cli": "commandcode",
                    "openai-oauth": "openai", "opencode-go": "opencode-go", "openrouter": "openrouter"}


def subagent_sources():
    """{quota source: [cc subagent names]} for cc's own-process subagents."""
    try:
        with open(os.path.expanduser("~/.config/cc/agents.json"), encoding="utf-8") as f:
            agents = json.load(f)
    except (OSError, ValueError):
        return {}
    found = {}
    for name, a in agents.items() if isinstance(agents, dict) else ():
        source = CC_QUOTA_SOURCES.get((a if isinstance(a, dict) else {}).get("provider", "official"))
        if source:
            found.setdefault(source, []).append(name)
    return found


def cc_profiles():
    try:
        with open(os.path.expanduser("~/.config/cc/agents.json"), encoding="utf-8") as handle:
            profiles = json.load(handle)
        return {name: profile for name, profile in profiles.items() if isinstance(profile, dict)} if isinstance(profiles, dict) else {}
    except (OSError, ValueError):
        return {}


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
    """Returns (quota, stale_since); a failed fetch keeps the last good result."""
    name = f"quota-{source}.json"
    saved = load_json(name)
    if now - max(saved.get("at", 0), saved.get("tried", 0)) < QUOTA_TTL:
        return saved, saved.get("at") if saved.get("tried", 0) > saved.get("at", 0) else None
    try:
        q = {**QUOTA_FETCHERS[source](), "at": now}
    except Exception:
        save_json(name, {**saved, "tried": now})  # don't retry (and block 3s) every render
        return saved, saved.get("at")
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
    mid = model.get("id") or ""
    # Claude keeps Anthropic's clay; other vendors get a stable hue from their name.
    hue = CLAUDE_HUE if not is_third_party(mid) else zlib.crc32(mid.split("/")[0].encode()) % 360
    head = f"{rgb(hue, 0.45)}{BOLD}{model.get('display_name') or mid or '?'}{RESET}"
    if effort:
        # Colored word plus one dot per level, cool for low effort, hot for max.
        head += " " + effort_indicator(effort)
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
    parts += quota_parts(plan, extra, limits, cached_at, now)

    # Configured targets are visible even on official Claude and even on the same provider.
    # Live worker rows separately identify native workers and the real cc relay/worker route.
    sub_lines = []
    child = os.environ.get("CC_AGENT_CHILD") == "1"
    configured = not child and cc_routing_enabled()
    if configured:
        main_provider = cc_main_provider(source)
        for name, profile in cc_profiles().items():
            provider = profile.get("provider", "official")
            mid = profile.get('model') or 'Claude default'
            target = re.sub(r'^(?:gpt|claude)-', '', mid.rsplit('/', 1)[-1], flags=re.I)
            target = re.sub(r'^(opus|sonnet|haiku)-(\d+)-(\d+)', r'\1 \2.\3', target).replace('-', ' ')
            labels = {'openai-oauth': 'OAuth', 'official': 'Claude', 'nvidia': 'NIM',
                      'commandcode': 'CmdC', 'commandcode-cli': 'CmdC CLI', 'minimax': 'MiniMax',
                      'opencode-go': 'Go', 'opencode-zen': 'Zen', 'openrouter': 'OpenRouter', 'gpt4free': 'g4f',
                      'gemini': 'Gemini', 'ollama-cloud': 'Ollama', 'lmstudio': 'LM Studio'}
            route = 'same provider' if provider == main_provider else labels.get(provider, provider)
            effort = profile.get('effort')
            dots = effort_indicator(effort, dim=True, label=False) if effort else DIM + 'default' + RESET
            hue = CLAUDE_HUE if not is_third_party(mid) else zlib.crc32(mid.split('/')[0].encode()) % 360
            seg = [f"{DIM}↳ {name}:{RESET} {rgb(hue, 0.45, 0.6)}{target}{RESET} {dots} · {route}"]
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
                seg += quota_parts(plan, q.get('extra'), q.get('rate_limits') or {}, sub_cached, now)
            sub_lines.append(SEP.join(seg))
    for sub_source, names in ({} if configured or child or source == "anthropic" else subagent_sources()).items():
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
