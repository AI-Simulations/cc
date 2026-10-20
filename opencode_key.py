"""Renew the OpenCode (Zen and Go) API key from a signed-in Chrome profile.

Like book writer's KDP session: the user signs in once in a plain Chrome window
using PROFILE; later renewals reuse that session headless, create a personal API
key in the console and store it where opencode keeps it. No password is stored.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

PROFILE = Path(os.getenv("CC_OPENCODE_PROFILE") or Path.home() / ".config" / "cc" / "browser_profile" / "opencode")
AUTH = Path.home() / ".local" / "share" / "opencode" / "auth.json"
ACCOUNT = "https://opencode.ai/console/account"
KEY_RE = re.compile(r"\b(?:oc_sk_|sk-)[A-Za-z0-9_-]{24,}")


class NotSignedIn(RuntimeError):
    pass


def key_ok(key):
    """False when OpenCode rejects the key, True when it accepts it, None when unknown (offline)."""
    req = urllib.request.Request("https://opencode.ai/zen/go/v1/usage",
                                 headers={"Authorization": "Bearer " + key, "User-Agent": "Mozilla/5.0 cc-switch"})
    try:
        urllib.request.urlopen(req, timeout=8).close()
        return True
    except urllib.error.HTTPError as e:
        body = e.read(400).decode("utf-8", "replace")
        if "error code: 10" in body:  # Cloudflare block, not a verdict on the key
            return None
        return False if e.code in (401, 403) or "api_key_expired" in body else None
    except (OSError, ValueError):
        return None


def _chrome():
    for path in (shutil.which("chrome"), os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
                 os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe")):
        if path and os.path.exists(path):
            return path
    raise RuntimeError("Chrome is not installed")


def sign_in():
    """Plain Chrome, not chromedriver: Google refuses sign-in in an automated browser."""
    PROFILE.mkdir(parents=True, exist_ok=True)
    print("Sign in to OpenCode in the Chrome window, then close that window.", file=sys.stderr)
    subprocess.run([_chrome(), f"--user-data-dir={PROFILE}", "--profile-directory=Default", "--no-first-run",
                    "https://opencode.ai/auth"])


def create_key():
    from selenium import webdriver
    from selenium.common.exceptions import StaleElementReferenceException, TimeoutException
    from selenium.webdriver.support.ui import WebDriverWait

    PROFILE.mkdir(parents=True, exist_ok=True)
    options = webdriver.ChromeOptions()
    for arg in (f"--user-data-dir={PROFILE}", "--profile-directory=Default", "--no-first-run",
                "--headless=new", "--window-size=1300,1000"):
        options.add_argument(arg)
    try:
        d = webdriver.Chrome(options=options)
    except Exception as exc:
        raise RuntimeError(f"Could not open the OpenCode Chrome profile at {PROFILE}; close any Chrome window using it. "
                           f"({type(exc).__name__})") from exc
    try:
        d.get(ACCOUNT)

        def add_button(c):
            if not c.current_url.startswith(ACCOUNT):
                raise NotSignedIn("The OpenCode Chrome profile is not signed in.")
            return next((e for e in c.find_elements("css selector", "button") if e.text.strip() == "Add API key"), None)

        WebDriverWait(d, 30).until(add_button).click()
        dialog = WebDriverWait(d, 15).until(lambda c: c.find_element("css selector", "[role=dialog] form"))
        dialog.find_element("css selector", "input[type=text]").send_keys(f"cc {date.today()}")
        # Restricted (the default): call models, and read usage for the status line.
        dialog.find_element("css selector", '[aria-label="Usage access"] [role=button]').click()
        WebDriverWait(d, 10).until(lambda c: next(
            (o for o in c.find_elements("css selector", "[role=option]") if o.text.strip() == "Read"), None)).click()
        d.find_element("css selector", "[role=dialog] button[type=submit]").click()  # footer, outside the form

        def new_key(c):
            for e in c.find_elements("css selector", "[role=dialog] input, [role=dialog] code, [role=dialog] pre"):
                m = KEY_RE.search(e.get_attribute("value") or e.text or "")
                if m:
                    return m.group(0)
            m = KEY_RE.search(" ".join(e.text for e in c.find_elements("css selector", "[role=dialog]")))
            return m.group(0) if m else None

        try:
            return WebDriverWait(d, 30, ignored_exceptions=(StaleElementReferenceException,)).until(new_key)
        except TimeoutException:
            shown = KEY_RE.sub("[key hidden]", " | ".join(
                e.text for e in d.find_elements("css selector", "[role=dialog], [role=alert]")))
            raise RuntimeError(f"The console showed no new key: {shown[:300] or 'no dialog'}") from None
    finally:
        d.quit()


def save(key):
    try:
        auth = json.loads(AUTH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        auth = {}
    for name in ("opencode", "opencode-go"):
        auth[name] = {"type": "api", "key": key}
    AUTH.parent.mkdir(parents=True, exist_ok=True)
    tmp = AUTH.with_suffix(".tmp")
    tmp.write_text(json.dumps(auth, indent=2), encoding="utf-8")
    os.replace(tmp, AUTH)


def renew(interactive=None):
    """Create, store and return a new key; opens a sign-in window first only when interactive."""
    if interactive is None:
        interactive = sys.stdin.isatty() and sys.stdout.isatty()
    try:
        key = create_key()
    except NotSignedIn:
        if not interactive:
            raise
        sign_in()
        key = create_key()
    save(key)
    return key


def stored_key():
    try:
        return json.loads(AUTH.read_text(encoding="utf-8"))["opencode-go"]["key"]
    except (OSError, ValueError, KeyError, TypeError):
        return ""


def main():
    key = stored_key()
    if "--force" not in sys.argv and key and key_ok(key) is not False:
        print("OpenCode key works.")
        return
    try:
        new = renew()
    except (NotSignedIn, RuntimeError) as exc:
        sys.exit(str(exc))
    print(f"New OpenCode key stored in {AUTH} ({new[:6]}…); check: {key_ok(new)}")


if __name__ == "__main__":
    main()
