"""Offline regressions for provider plan labels, including cached values."""
import importlib.util
import io
import json
import os
import re
import tempfile
import time
import unittest
from unittest.mock import patch
from pathlib import Path

spec = importlib.util.spec_from_file_location("statusline", Path(__file__).with_name("statusline.py"))
statusline = importlib.util.module_from_spec(spec)
spec.loader.exec_module(statusline)


class PlanLabelTests(unittest.TestCase):
    def test_openai_plan_labels(self):
        for raw, expected in (("prolite", "ChatGPT Pro Lite"), ("pro", "ChatGPT Pro"), (None, "ChatGPT")):
            with self.subTest(raw=raw), patch("builtins.open", return_value=io.StringIO(json.dumps({"tokens": {"access_token": "test"}}))), patch.object(statusline, "fetch_json", return_value={"plan_type": raw}):
                self.assertEqual(statusline.openai_quota()["plan"], expected)

    def test_provider_tokens_without_api_label(self):
        with patch.object(statusline, "quota_source", return_value=None), patch.object(statusline, "settings_effort", return_value=None), patch.object(statusline, "settings_ultracode", return_value=False), patch.object(statusline, "keyword_ultracode", return_value=False):
            rendered = statusline.render({"model": {"id": "gpt-test"}, "context_window": {"total_input_tokens": 6000, "total_output_tokens": 476}})
        self.assertNotIn("↑", rendered)
        self.assertNotIn("API", rendered)

    def test_third_party_cache_uses_reported_reads(self):
        with patch.object(statusline, "quota_source", return_value=None), patch.object(statusline, "settings_effort", return_value=None), patch.object(statusline, "settings_ultracode", return_value=False), patch.object(statusline, "keyword_ultracode", return_value=False):
            payload = {"model": {"id": "gpt-test"}, "prompt_cache": {"warm": False}, "context_window": {"current_usage": {"input_tokens": 1000, "cache_read_input_tokens": 4000}}}
            rendered = statusline.render(payload)
            self.assertNotIn("cache read", rendered)
            self.assertNotIn("cache off", rendered)
            payload["context_window"]["current_usage"] = {"input_tokens": 1000}
            rendered = statusline.render(payload)
            self.assertNotIn("cache off", rendered)
            self.assertNotIn("cache read", rendered)

    def test_adapter_route_shows_recorded_rate_limits(self):
        saved = {"at": 1000, "rate_limits": {"req": {"used_percentage": 75, "resets_at": None}}}
        with patch.object(statusline, "quota_source", return_value=None), patch.object(statusline, "settings_effort", return_value=None), patch.object(statusline, "settings_ultracode", return_value=False), patch.object(statusline, "keyword_ultracode", return_value=False), \
                patch.object(statusline, "load_json", side_effect=lambda name: saved if name == "ratelimit-gemini.json" else {}), patch.dict(os.environ, {"CC_MAIN_PROVIDER": "gemini"}):
            rendered = statusline.render({"model": {"id": "gemini-2.5-pro"}})
        self.assertIn("req", rendered)
        self.assertIn("75%", rendered)

    def test_fallback_shown_and_highlighted_while_serving(self):
        files = {"fallbacks.json": {"L": {"provider": "gemini", "model": "gemini-2.5-flash"}}}
        def load(name):
            return files.get(os.path.basename(name), {})
        with patch.object(statusline, "quota_source", return_value=None), patch.object(statusline, "settings_effort", return_value=None), patch.object(statusline, "settings_ultracode", return_value=False), patch.object(statusline, "keyword_ultracode", return_value=False), \
                patch.object(statusline, "load_json", side_effect=load), patch.dict(os.environ, {"CC_MAIN_PROVIDER": "nvidia", "CC_LAUNCH_ID": "L"}):
            idle = statusline.render({"model": {"id": "deepseek-v4-pro"}})
            files["fallback-L.json"] = {"at": time.time()}
            serving = statusline.render({"model": {"id": "deepseek-v4-pro"}})
            os.environ["CC_LAUNCH_ID"] = "other"
            unset = statusline.render({"model": {"id": "deepseek-v4-pro"}})
        self.assertIn("↪ fallback gemini/gemini-2.5-flash", idle)
        self.assertNotIn("active", idle)
        self.assertIn("fallback gemini/gemini-2.5-flash active", serving)
        self.assertNotIn("fallback", unset)

    def test_cached_label_display(self):
        quota = {"plan": "ChatGPT Prolite", "rate_limits": {"seven_day": {"used_percentage": 0}}}
        with patch.object(statusline, "quota_source", return_value="openai"), patch.object(statusline, "provider_quota", return_value=(quota, None)), patch.object(statusline, "settings_effort", return_value=None), patch.object(statusline, "settings_ultracode", return_value=False), patch.object(statusline, "keyword_ultracode", return_value=False):
            rendered = statusline.render({"model": {"id": "gpt-test"}})
        self.assertIn("ChatGPT Pro Lite", rendered)
        self.assertNotIn("ChatGPT Prolite", rendered)
        self.assertEqual(quota["plan"], "ChatGPT Prolite")


class ApiBillingTests(unittest.TestCase):
    """The session cost segment: shown when an API key pays, absent when the plan does."""

    # What a subscription session leaves behind in ~/.claude/statusline/quota.json.
    CACHED = {"rate_limits": {"seven_day": {"used_percentage": 43, "resets_at": 0}}, "at": 1, "account": None}

    def render(self, payload, stored, env, creds=None):
        with patch.dict(os.environ, env, clear=True), patch.object(statusline, "claude_json", return_value=stored), patch.object(statusline, "credentials", return_value=creds or {}), patch.object(statusline, "quota_source", return_value="anthropic"), patch.object(statusline, "load_json", return_value=self.CACHED), patch.object(statusline, "save_json"), patch.object(statusline, "settings_effort", return_value=None), patch.object(statusline, "settings_ultracode", return_value=False), patch.object(statusline, "keyword_ultracode", return_value=False):
            rendered = statusline.render(payload)
        return re.sub(r"\x1b\[[0-9;]*m", "", rendered)  # assertions read the text, not the colours

    def plan_payload(self, cost):
        return {"model": {"id": "claude-opus-5-5", "display_name": "Opus 5.5"}, "cost": {"total_cost_usd": cost},
                "rate_limits": {"seven_day": {"used_percentage": 43, "resets_at": 0}}}

    def test_api_key_shows_session_cost_not_plan_windows(self):
        key = "sk-ant-usr-" + "x" * 20
        rendered = self.render(self.plan_payload(1.84), {}, {"ANTHROPIC_API_KEY": key})
        self.assertIn("api $1.84", rendered)
        self.assertNotIn("7d", rendered)

    def test_declined_key_leaves_the_plan_in_charge(self):
        key = "sk-ant-usr-" + "y" * 20
        stored = {"customApiKeyResponses": {"rejected": [key[-20:]]}}
        rendered = self.render(self.plan_payload(1.84), stored, {"ANTHROPIC_API_KEY": key})
        self.assertIn("7d", rendered)
        self.assertNotIn("api $", rendered)

    def test_console_stored_key_bills_and_shows_sub_cent_cost(self):
        payload = {"model": {"id": "claude-opus-5-5", "display_name": "Opus 5.5"}, "cost": {"total_cost_usd": 0.084}}
        rendered = self.render(payload, {"primaryApiKey": "sk-ant-api03-x"}, {})
        self.assertIn("api $0.084", rendered)
        self.assertNotIn("7d", rendered)

    def test_expired_plan_windows_do_not_look_like_api_billing(self):
        # Claude Code drops each window once it resets, so a subscriber mid-session can send an
        # empty rate_limits object; that is still a plan session, not a keyed one.
        payload = {"model": {"id": "claude-opus-5-5", "display_name": "Opus 5.5"},
                   "cost": {"total_cost_usd": 1.84}, "rate_limits": {}}
        rendered = self.render(payload, {"primaryApiKey": "sk-ant-api03-x"}, {})
        self.assertNotIn("api $", rendered)
        self.assertIn("7d", rendered)  # the window replayed from the cache


    def test_api_cost_shows_at_zero_before_the_first_reply(self):
        # Nothing spent yet: the segment is the only sign the key is paying for this session.
        payload = {"model": {"id": "claude-opus-5-5", "display_name": "Opus 5.5"}}
        rendered = self.render(payload, {"primaryApiKey": "sk-ant-api03-x"}, {})
        self.assertIn("api $0.000", rendered)


    def test_claude_ai_login_keeps_api_usage_off_the_line(self):
        # Signed in to claude.ai the plan pays, so no spend and no api label, even before the
        # first reply brings rate_limits, and even with a Console key still stored.
        payload = {"model": {"id": "claude-opus-5-5", "display_name": "Opus 5.5"}, "cost": {"total_cost_usd": 1.84}}
        rendered = self.render(payload, {"primaryApiKey": "sk-ant-api03-x"}, {}, {"claudeAiOauth": {"accessToken": "x"}})
        self.assertNotIn("api $", rendered)

    def test_replayed_window_is_marked_approximate(self):
        # Payload carries no rate_limits, so the window below comes from the cache, not live data.
        payload = {"model": {"id": "claude-opus-5-5", "display_name": "Opus 5.5"}}
        rendered = self.render(payload, {}, {})
        self.assertIn("~7d", rendered)
        self.assertNotIn("old", rendered)  # the cache age is not worth the width

    def test_reset_countdown_has_a_space(self):
        # Assert the shape, not an exact duration: the countdown rounds down between building
        # the payload and rendering it.
        payload = {"model": {"id": "claude-opus-5-5", "display_name": "Opus 5.5"},
                   "rate_limits": {"five_hour": {"used_percentage": 100, "resets_at": time.time() + 3600}}}
        rendered = self.render(payload, {}, {})
        self.assertRegex(rendered, r"↻ \d")
        self.assertNotIn(r"↻\d", rendered)


class SubagentLineTests(unittest.TestCase):
    """Legacy quota lines when configured-worker routing is not installed."""

    def render(self, source, subs):
        with patch.object(statusline, "quota_source", return_value=source), \
             patch.object(statusline, "cc_routing_enabled", return_value=False), \
             patch.object(statusline, "subagent_sources", return_value=subs), \
             patch.object(statusline, "provider_quota", return_value=({"plan": "Command Code", "extra": "$1.00 left", "rate_limits": {}}, None)), \
             patch.object(statusline, "claude_json", return_value={}), \
             patch.object(statusline, "credentials", return_value={}), \
             patch.object(statusline, "load_json", return_value={}), \
             patch.object(statusline, "save_json"), \
             patch.object(statusline, "settings_effort", return_value=None), \
             patch.object(statusline, "settings_ultracode", return_value=False), \
             patch.object(statusline, "keyword_ultracode", return_value=False):
            return re.sub(r"\x1b\[[0-9;]*m", "", statusline.render(
                {"model": {"id": "claude-opus-5-5", "display_name": "Opus 5.5"}}))

    def test_official_claude_shows_no_cc_subagent_data(self):
        rendered = self.render("anthropic", {"commandcode": ["sub"]})
        self.assertNotIn("subagent", rendered)
        self.assertNotIn("Command Code", rendered)

    def test_quota_source_route_detection(self):
        # The gate above depends on this mapping: a typo would re-enable the subagent line.
        for env, expected in (({}, "anthropic"),
                              ({"ANTHROPIC_BASE_URL": "https://api.anthropic.com"}, "anthropic"),
                              ({"ANTHROPIC_BASE_URL": "http://127.0.0.1:10532/commandcode"}, "commandcode"),
                              ({"ANTHROPIC_BASE_URL": "http://127.0.0.1:10532/opencode-go"}, "opencode-go")):
            with self.subTest(env=env), patch.dict(os.environ, env, clear=True):
                self.assertEqual(statusline.quota_source(), expected)

    def test_third_party_session_still_shows_other_account_subagents(self):
        rendered = self.render("openai", {"commandcode": ["sub"]})
        self.assertIn("↳ subagent sub", rendered)

    def test_unknown_route_still_shows_the_subagent_line(self):
        self.assertIn("↳ subagent sub", self.render(None, {"commandcode": ["sub"]}))


class QuotaErrorTests(unittest.TestCase):
    def test_an_expired_key_is_named_instead_of_hiding_the_bars(self):
        body = io.BytesIO(b'{"error":{"code":"api_key_expired"}}')
        expired = statusline.urllib.error.HTTPError("https://x", 401, "Unauthorized", {}, body)
        with patch.dict(statusline.QUOTA_FETCHERS, {"opencode-go": lambda: (_ for _ in ()).throw(expired)}), \
             patch.object(statusline, "load_json", return_value={}), patch.object(statusline, "save_json") as saved:
            q, _ = statusline.provider_quota("opencode-go", 1000.0)
        self.assertEqual(q["error"], "key expired")
        self.assertEqual(saved.call_args.args[1]["error"], "key expired")


class ConfiguredAgentLineTests(unittest.TestCase):
    def render(self, main, profiles, quota=None, ansi=False):
        hooks = {'PreToolUse': [{'matcher': 'Agent|Task', 'hooks': [
            {'type': 'command', 'command': 'python cc-switch.py hook-agent'}]}]}
        with patch.dict(os.environ, {'CC_MAIN_PROVIDER': main, 'CC_AGENT_CHILD': ''}), \
             patch.object(statusline, 'user_settings', return_value={'hooks': hooks}), \
             patch.object(statusline, 'cc_profiles', return_value=profiles, create=True), \
             patch.object(statusline, 'quota_source', return_value='anthropic' if main == 'official' else 'openai'), \
             patch.object(statusline, 'provider_quota', return_value=(quota or {}, None)), \
             patch.object(statusline, 'claude_json', return_value={}), \
             patch.object(statusline, 'credentials', return_value={}), \
             patch.object(statusline, 'load_json', return_value={}), \
             patch.object(statusline, 'save_json'), \
             patch.object(statusline, 'keyword_ultracode', return_value=False):
            text = statusline.render({'model': {'id': 'main-model'}})
        return text if ansi else re.sub(r'\x1b\[[0-9;]*m', '', text)

    def test_official_main_shows_configured_worker_provider_model_and_effort(self):
        text = self.render('official', {'sub': {'provider': 'openai-oauth', 'model': 'gpt-test', 'effort': 'xhigh'}})
        self.assertIn('sub:', text)
        self.assertIn('test', text)
        self.assertIn('OAuth', text)
        self.assertIn('●●●●', text)

    def test_same_provider_is_visible_and_explicitly_marked(self):
        for provider in ('official', 'openai-oauth'):
            with self.subTest(provider=provider):
                text = self.render(provider, {'sub': {'provider': provider, 'model': 'worker-model'}})
                self.assertIn('worker model', text)
                self.assertIn('same provider', text)

    def test_provider_without_quota_api_is_still_visible(self):
        text = self.render('official', {'sub': {'provider': 'nvidia', 'model': 'worker-model'}})
        self.assertIn('NIM', text)
        self.assertIn('worker model', text)
        self.assertNotIn('same provider', text)

    def test_configured_worker_line_has_short_labels_and_keeps_quota_bars(self):
        text = self.render('official', {'sub': {'provider': 'openai-oauth', 'model': 'gpt-6.1-sol', 'effort': 'xhigh'}})
        line = text.splitlines()[-1]
        self.assertEqual(line, '↳ sub: 6.1 sol ●●●● · OAuth')
        quota = {'plan': 'ChatGPT Plus', 'rate_limits': {
            'five_hour': {'used_percentage': 81, 'resets_at': None},
            'seven_day': {'used_percentage': 12, 'resets_at': None}}}
        line = self.render('official', {'sub': {'provider': 'openai-oauth', 'model': 'gpt-6.1-sol', 'effort': 'xhigh'}}, quota).splitlines()[-1]
        self.assertIn('Plus', line)
        self.assertNotIn('ChatGPT', line)
        self.assertIn('5h ▰▰▰▰▰▱ 81%', line)
        self.assertIn('7d ▰▱▱▱▱▱ 12%', line)
        self.assertLess(len(line), 90)

    def test_effort_circles_share_main_mapping_with_lower_brightness(self):
        for number, (effort, hue) in enumerate(statusline.EFFORT_HUES.items(), 1):
            with self.subTest(effort=effort):
                line = self.render('official', {'sub': {'provider': 'nvidia', 'model': 'gpt-test', 'effort': effort}}, ansi=True).splitlines()[-1]
                self.assertIn(statusline.rgb(hue, val=0.55) + '●' * number, line)
                self.assertNotIn(statusline.rgb(hue) + '●' * number, line)


class EffortLevelTests(unittest.TestCase):
    def test_default_ladder_keeps_the_historical_circles_and_hues(self):
        for number, (effort, hue) in enumerate(statusline.EFFORT_HUES.items(), 1):
            with self.subTest(effort=effort):
                got = statusline.effort_indicator(effort, label=False)
                self.assertEqual(got, statusline.rgb(hue, val=0.9) + '●' * number + statusline.RESET)
                self.assertEqual(got, statusline.effort_indicator(effort, label=False, levels=list(statusline.EFFORT_HUES)))

    def test_circles_and_hue_follow_the_models_own_ladder(self):
        three = ['low', 'medium', 'high']
        top = statusline.effort_indicator('high', label=False, levels=three)
        self.assertEqual(top, statusline.rgb(statusline.EFFORT_HUES['max'], val=0.9) + '●' * 3 + statusline.RESET)
        mid = statusline.effort_indicator('medium', label=False, levels=three)
        self.assertEqual(mid, statusline.rgb(statusline.EFFORT_HUES['high'], val=0.9) + '●' * 2 + statusline.RESET)
        self.assertEqual(statusline.effort_indicator('max', label=True, levels=three),
                         statusline.rgb(280, val=0.9) + 'max' + statusline.RESET)
        self.assertEqual(statusline.effort_indicator('max', label=False, levels=three),
                         statusline.rgb(280, val=0.9) + 'max' + statusline.RESET)

    def test_off_is_the_step_below_low_on_models_that_offer_it(self):
        ladder = ['off', 'high', 'max']
        self.assertEqual(statusline.effort_indicator('off', label=False, levels=ladder),
                         statusline.rgb(statusline.EFFORT_HUES['low'], val=0.9) + '●' + statusline.RESET)
        self.assertEqual(statusline.effort_indicator('max', label=False, levels=ladder),
                         statusline.rgb(statusline.EFFORT_HUES['max'], val=0.9) + '●●●' + statusline.RESET)

    def test_model_without_adjustable_effort_renders_default(self):
        self.assertEqual(statusline.effort_indicator('high', label=False, levels=[]),
                         statusline.DIM + 'default' + statusline.RESET)

    def test_hue_ramp_is_cool_at_the_bottom_and_hot_at_the_top(self):
        self.assertAlmostEqual(statusline.effort_hue(0.0), statusline.EFFORT_HUES['low'], places=3)
        self.assertAlmostEqual(statusline.effort_hue(1.0), statusline.EFFORT_HUES['max'], places=3)
        self.assertAlmostEqual(statusline.effort_hue(0.5), statusline.EFFORT_HUES['high'], places=3)

    def test_levels_are_read_from_the_provider_that_serves_the_model(self):
        cache = {"models": [{"slug": "gpt-x", "supported_reasoning_levels": [
            {"effort": "low"}, {"effort": "high"}, {"effort": "max"}]},
            {"slug": "gpt-y", "supported_reasoning_levels": [{"effort": "low"}]}]}
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, 'models_cache.json').write_text(json.dumps(cache), encoding='utf-8')
            with patch.dict(os.environ, {'CODEX_HOME': tmp}):
                self.assertEqual(statusline.effort_levels('openai-oauth', 'GPT-X'), ['low', 'high', 'max'])
                self.assertIsNone(statusline.effort_levels('openai-oauth', 'gpt-z'))
        saved = {'at': time.time(), 'models': {'MiniMax-M3': ['low', 'medium', 'high'], 'mimo': []}}
        with patch.object(statusline, 'read_json', return_value=saved):
            self.assertEqual(statusline.effort_levels('commandcode-cli', 'minimax-m3'), ['low', 'medium', 'high'])
            self.assertEqual(statusline.effort_levels('commandcode', 'mimo'), [])
            self.assertIsNone(statusline.effort_levels('nvidia', 'minimax-m3'))


if __name__ == "__main__":
    unittest.main()
