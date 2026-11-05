"""Per-session subagent profiles, /model route overrides and relay stop nudges."""
import importlib.util
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent


def load_switch():
    spec = importlib.util.spec_from_file_location("cc_switch_under_test", HERE / "cc-switch.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CcSessionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.cc = load_switch()
        self.cc.CFG_DIR = str(root)
        self.cc.AGENTS_FILE = str(root / "agents.json")
        self.cc.SESSION_AGENTS_FILE = str(root / "session-agents.json")
        self.cc.ROUTES_FILE = str(root / "routes.json")
        self.cc.FALLBACKS_FILE = str(root / "fallbacks.json")
        self.cc.CLAUDE_SETTINGS = str(root / "claude-settings.json")
        self.cc.JOBS_DIR = str(root / "jobs")
        self.cc.AGENTS_DIR = str(root / "agents")
        self.cc.sync_agents = lambda: None
        (root / "jobs").mkdir()
        self.global_agents = {"sub": {"provider": "official", "effort": "high"}}
        (root / "agents.json").write_text(json.dumps(self.global_agents), encoding="utf-8")
        self.old_launch = os.environ.get("CC_LAUNCH_ID")
        os.environ["CC_LAUNCH_ID"] = "launch-a"
        self.claudecode = patch.dict(os.environ, {"CLAUDECODE": "1"})  # edits come from inside the session
        self.claudecode.start()

    def tearDown(self):
        self.claudecode.stop()
        if self.old_launch is None:
            os.environ.pop("CC_LAUNCH_ID", None)
        else:
            os.environ["CC_LAUNCH_ID"] = self.old_launch
        self.tmp.cleanup()

    def test_subagent_edit_is_isolated_to_the_launch(self):
        changed = {"sub": {"provider": "official", "effort": "low"}}
        self.cc.save_agents(changed)
        self.assertEqual(self.cc.load_agents()["sub"]["effort"], "low")
        os.environ["CC_LAUNCH_ID"] = "launch-b"
        self.assertEqual(self.cc.load_agents()["sub"]["effort"], "high")
        self.assertEqual(json.loads(Path(self.cc.AGENTS_FILE).read_text()), self.global_agents)

    def test_subagents_off_is_per_session_and_survives_profile_edits(self):
        call = {"tool_name": "Agent", "tool_input": {"subagent_type": "cc-sub", "prompt": "x"}}
        with patch("sys.stdout", new_callable=io.StringIO):
            self.cc.set_subagents_off(True)
        self.assertEqual(self.cc.load_agents(), self.global_agents, "off keeps the global profiles")
        self.assertEqual(self.cc.agent_hook(call)["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn("Subagents are off", self.cc.hook_context({})["hookSpecificOutput"]["additionalContext"])
        self.cc.save_agents({"sub": {"provider": "official", "effort": "low"}})
        self.assertTrue(self.cc.subagents_off(), "a profile edit keeps the switch")
        self.cc.link_session("sid-1")
        os.environ["CC_LAUNCH_ID"] = "launch-b"
        self.assertFalse(self.cc.subagents_off(), "other sessions keep their subagents")
        self.cc.link_session("sid-1")
        self.assertTrue(self.cc.subagents_off(), "a resume keeps the switch")
        with patch("sys.stdout", new_callable=io.StringIO):
            self.cc.set_subagents_off(False)
        self.assertFalse(self.cc.subagents_off())
        self.assertEqual(self.cc.load_agents()["sub"]["effort"], "low")

    def test_new_launch_starts_on_the_saved_provider_preset(self):
        bundle = {"model": {"provider": "official", "model": "claude-opus-5-5", "effort": "medium"},
                  "subagent": {"provider": "official", "model": "claude-haiku-5-5", "effort": "max"},
                  "fallback": {"provider": "off"},
                  "auto": {"provider": "official", "model": "claude-haiku-5-5", "effort": "low"},
                  "tools": {"provider": "official", "model": "claude-haiku-5-5", "effort": "max"}}
        Path(self.cc.CFG_DIR, "presets.json").write_text(json.dumps({"official": {"model": "claude-opus-5-5", "settings": bundle}}), encoding="utf-8")
        self.assertEqual(self.cc.default_effort("official", "claude-opus-5-5"), "medium")
        self.assertIsNone(self.cc.default_effort("official", "claude-sonnet-5-5"))
        self.cc.seed_launch_from_preset("official", "launch-a", adapter=True, providers=list(self.cc.PROVIDERS))
        self.assertEqual(self.cc.load_agents()["sub"], {"provider": "official", "model": "claude-haiku-5-5", "effort": "max"})
        self.assertEqual(self.cc.load_routes()["launch-a"]["helpers"]["auto"]["model"], "claude-haiku-5-5")
        self.assertEqual(json.loads(Path(self.cc.AGENTS_FILE).read_text()), self.global_agents, "global profiles untouched")
        self.assertIsNone(self.cc.preset_fallback("official"))
        os.environ["CC_LAUNCH_ID"] = "launch-b"
        self.cc.save_agents({"sub": {"provider": "official", "effort": "low"}})
        self.cc.seed_launch_from_preset("official", "launch-b", adapter=False, providers=[])
        self.assertEqual(self.cc.load_agents()["sub"]["effort"], "low", "an explicit pick wins over the preset")

    def test_resume_keeps_the_sessions_subagents(self):
        self.cc.save_agents({"sub": {"provider": "official", "effort": "medium"}})
        self.cc.link_session("sid-1")
        os.environ["CC_LAUNCH_ID"] = "launch-resumed"
        self.assertEqual(self.cc.load_agents()["sub"]["effort"], "high")
        self.cc.link_session("sid-1")
        self.assertEqual(self.cc.load_agents()["sub"]["effort"], "medium")

    def test_agent_runs_in_process_through_the_adapter_and_relays_otherwise(self):
        root = Path(self.tmp.name)
        main = root / "main.jsonl"
        listing = {"type": "attachment", "attachment": {"type": "agent_listing_delta", "addedTypes": ["cc-sub-live"], "removedTypes": []}}
        main.write_text(json.dumps(listing) + "\n", encoding="utf-8")
        call = {"tool_name": "Agent", "transcript_path": str(root / "main.jsonl"),
                "tool_input": {"subagent_type": "cc-sub", "prompt": "do it", "description": "d"}}
        with patch.dict(os.environ, {"ANTHROPIC_BASE_URL": f"http://127.0.0.1:{self.cc.ADAPTER_PORT}/official"}):
            live = self.cc.agent_hook(call)["hookSpecificOutput"]["updatedInput"]
            self.assertEqual((live["subagent_type"], live["prompt"]), ("cc-sub-live", "do it"))
            main.write_text('{"type":"attachment","attachment":{"type":"agent_listing_delta","addedTypes":["cc-sub"]}}\n', encoding="utf-8")
            fresh = self.cc.agent_hook(call)["hookSpecificOutput"]["updatedInput"]
            self.assertEqual(fresh["subagent_type"], "cc-sub", "A session that never listed the twin relays")
        with patch.dict(os.environ, {"ANTHROPIC_BASE_URL": ""}):
            relayed = self.cc.agent_hook({**call, "tool_input": {**call["tool_input"], "subagent_type": "cc-sub-live"}})
        updated = relayed["hookSpecificOutput"]["updatedInput"]
        self.assertEqual(updated["subagent_type"], "cc-sub", "A direct session relays even a cc-*-live call")
        self.assertTrue(updated["prompt"].startswith(self.cc.CONTEXT_PREFIX))

    def test_worker_context_cuts_a_long_unattended_turn_at_a_step(self):
        prompt = {"type": "user", "uuid": "p", "message": {"role": "user", "content": "go"}}
        turn = []
        for i in range(10):
            turn.append({"type": "assistant", "uuid": f"a{i}", "message": {"role": "assistant", "content": "x" * 4000}})
            turn.append({"type": "user", "uuid": f"r{i}", "message": {"role": "user", "content": [{"type": "tool_result", "content": "y"}]}})
        tail = self.cc._context_tail([prompt, *turn], budget=2500)
        self.assertEqual([e["uuid"] for e in tail], ["p", "a8", "r8", "a9", "r9"])
        self.assertEqual(tail[1]["parentUuid"], "p")
        self.assertEqual(len(self.cc._context_tail([prompt, *turn], budget=100000)), 21, "A turn that fits stays whole")

    def test_a_terminal_left_with_an_old_launch_id_edits_the_global_profiles(self):
        changed = {"sub": {"provider": "official", "effort": "low"}}
        with patch.dict(os.environ, {"CLAUDECODE": ""}):
            self.cc.save_agents(changed)
        self.assertEqual(json.loads(Path(self.cc.AGENTS_FILE).read_text()), changed)
        self.assertFalse(Path(self.cc.SESSION_AGENTS_FILE).exists())

    def test_startup_prompt_offers_the_last_launch_picks(self):
        last = {"sub": {"provider": "opencode-go", "model": "mimo-v2.6-pro", "effort": "high"}}
        Path(self.cc.SESSION_AGENTS_FILE).write_text(json.dumps({"old": {"agents": last, "at": 1}}), encoding="utf-8")
        os.environ["CC_LAUNCH_ID"] = "new-launch"
        shown = {}
        with patch.object(self.cc.sys.stdin, "isatty", return_value=True),                 patch.object(self.cc.sys.stdout, "isatty", return_value=True),                 patch.object(self.cc, "edit_agent", side_effect=lambda agents, *a, **k: shown.update(agents)):
            self.cc.startup_subagent()
        self.assertEqual(shown, last)
        self.assertEqual(self.cc.load_agents(), last, "keeping the offered pick keeps it for this launch")

    def test_fallback_refuses_a_session_that_connects_directly(self):
        with patch.dict(os.environ, {"CC_MAIN_PROVIDER": "nvidia"}), patch("sys.stdout", new_callable=io.StringIO) as out:
            self.cc.fallback_command(["nvidia", "deepseek-ai/x"])
        self.assertIn("Fallback: nvidia/deepseek-ai/x", out.getvalue())
        with patch.dict(os.environ, {"CC_MAIN_PROVIDER": "official", "ANTHROPIC_BASE_URL": ""}), self.assertRaises(SystemExit):
            self.cc.fallback_command([])

    def test_tools_default_low_only_when_supported(self):
        for provider, levels, expected in [('openai-oauth', ['low', 'high'], 'low'),
                                            ('commandcode-cli', ['high', 'max'], None),
                                            ('nvidia', None, None), ('commandcode-cli', [], None),
                                            ('official', None, 'low')]:
            with self.subTest(provider=provider, levels=levels), patch.object(self.cc, 'model_efforts', return_value=levels):
                self.assertEqual(self.cc.lowest_effort(provider, 'main-model'), expected)

    def test_sol_default_is_high_without_overriding_explicit_effort(self):
        cc = self.cc
        with patch.object(cc, 'route_env', return_value={}), patch.object(cc, 'load_state', return_value={}), \
                patch.object(cc, 'save_state'), patch.object(cc, 'write_apply') as write, \
                patch('sys.stdout', new_callable=io.StringIO):
            cc.apply('openai-oauth', 'gpt-6.1-sol')
            self.assertEqual(write.call_args.args[3], ['--effort', 'high'])
            cc.apply('openai-oauth', 'gpt-6.1-sol', effort='low')
            self.assertEqual(write.call_args.args[3], ['--effort', 'low'])
            cc.apply('openai-oauth', 'gpt-6.1-sol', claude_args=['--effort=medium'])
            self.assertEqual(write.call_args.args[3], ['--effort=medium'])
            cc.apply('nvidia', 'test/model')
            self.assertEqual(write.call_args.args[3], ())
        cc.set_route('launch-a', 'openai-oauth', 'gpt-6.1-sol')
        self.assertEqual(cc.load_routes()['launch-a']['effort'], 'high')
        cc.set_route('launch-a', 'openai-oauth', 'gpt-6.1-sol', 'low')
        self.assertEqual(cc.load_routes()['launch-a']['effort'], 'low')

    def test_plain_effort_default_is_launch_only_and_resume_is_untouched(self):
        cc = self.cc
        for args, expected in [([], 'medium'), (['--effort', 'high'], 'medium'),
                               (['--resume', 'sid'], None), (['-c'], None),
                               (['--model', 'sonnet'], None)]:
            with self.subTest(args=args), patch.object(cc.sys, 'argv', ['cc', 'plain', *args]), \
                    patch.object(cc, 'APPLY_FILE', str(Path(self.tmp.name) / 'apply.ps1')), \
                    patch.object(cc, 'quit_on_ctrl_c'), patch.object(cc, 'apply') as apply:
                cc.main()
            self.assertEqual(apply.call_args.kwargs['effort'], expected)
            self.assertEqual(apply.call_args.kwargs['claude_args'], args)
        with patch.object(cc, 'route_env', return_value={}), patch.object(cc, 'load_state', return_value={}), \
                patch.object(cc, 'save_state'), patch.object(cc, 'write_apply') as write, \
                patch('sys.stdout', new_callable=io.StringIO):
            cc.apply('official', effort='medium', claude_args=['--effort', 'high'])
            self.assertEqual(write.call_args.args[3], ['--effort', 'high'])

    def test_official_keeps_the_adapter_and_counts_as_first_party(self):
        adapter = f"http://127.0.0.1:{self.cc.ADAPTER_PORT}/official"
        flag = "_CLAUDE_CODE_ASSUME_FIRST_PARTY_BASE_URL"
        self.assertIn(flag, self.cc.CC_VARS, "other routes clear a flag left by an official launch")
        for env, expected in [({}, adapter), ({"CC_OFFICIAL_DIRECT": "1"}, None)]:
            with self.subTest(env=env), patch.dict(os.environ, {"CC_OFFICIAL_DIRECT": "", **env}), \
                    patch.object(self.cc, "ensure_anthropic_proxy"):
                got = self.cc.route_env("official", None, passthrough=True)
            self.assertEqual(got.get("ANTHROPIC_BASE_URL"), expected)
            # Claude Code keeps its first-party helpers (Haiku agent summaries) behind the adapter
            self.assertEqual(got.get(flag), "1" if expected else None)

    def test_official_route_effort_reaches_claude_settings(self):
        settings = Path(self.tmp.name) / 'settings.json'
        settings.write_text(json.dumps({'theme': 'dark', 'modelSettings': {'claude-opus-5-5': {
            'effortLevel': 'high', 'autoCompactWindow': 400000}}}), encoding='utf-8')
        with patch.object(self.cc, 'CLAUDE_SETTINGS', str(settings)):
            self.cc.set_route('launch-a', 'official', 'claude-opus-5-5', 'medium')
            data = json.loads(settings.read_text(encoding='utf-8'))
            self.assertEqual(data['modelSettings']['claude-opus-5-5'], {'effortLevel': 'medium', 'autoCompactWindow': 400000})
            self.assertEqual(data['theme'], 'dark')
            for args in [('official', 'claude-opus-5-5', 'max'), ('openai-oauth', 'claude-opus-5-5', 'low')]:
                self.cc.set_route('launch-a', *args)
            self.assertEqual(json.loads(settings.read_text(encoding='utf-8')), data)

    def test_route_override_set_and_clear(self):
        self.cc.set_route("launch-a", "gemini", "gemini-3.8-flash")
        self.assertEqual(self.cc.load_routes()["launch-a"]["model"], "gemini-3.8-flash")
        self.cc.set_route("launch-a")
        self.assertNotIn("launch-a", self.cc.load_routes())

    def test_helper_picks_preserve_main_route_and_other_sessions(self):
        self.cc.set_route('launch-a', 'openai-oauth', 'gpt-6.1-sol', 'high')
        self.cc.set_route('launch-b', 'gemini', 'gemini-test')
        with patch.dict(os.environ, {'ANTHROPIC_BASE_URL': f'http://127.0.0.1:{self.cc.ADAPTER_PORT}/official'}), patch('sys.stdout', new_callable=io.StringIO):
            self.cc.helper_command(['auto', 'openai-oauth', 'gpt-6-luna', 'low'])
            self.cc.helper_command(['tools', 'openai-oauth', 'gpt-6-luna', 'medium'])
        row = self.cc.load_routes()['launch-a']
        self.assertEqual(row['model'], 'gpt-6.1-sol')
        self.assertEqual(row['effort'], 'high')
        self.assertEqual(row['helpers']['auto']['effort'], 'low')
        self.assertEqual(row['helpers']['tools']['effort'], 'medium')
        self.cc.set_route('launch-a', 'nvidia', 'other')
        self.assertEqual(self.cc.load_routes()['launch-a']['helpers'], row['helpers'])
        self.cc.set_route('launch-a')
        self.assertNotIn('model', self.cc.load_routes()['launch-a'])
        self.assertEqual(self.cc.load_routes()['launch-a']['helpers'], row['helpers'])
        self.assertEqual(self.cc.load_routes()['launch-b']['model'], 'gemini-test')
        with patch.dict(os.environ, {'ANTHROPIC_BASE_URL': f'http://127.0.0.1:{self.cc.ADAPTER_PORT}/official'}), patch('sys.stdout', new_callable=io.StringIO):
            self.cc.helper_command(['auto', 'inherit'])
        self.assertNotIn('auto', self.cc.load_routes()['launch-a']['helpers'])
        self.assertIn('tools', self.cc.load_routes()['launch-a']['helpers'])

    def test_helper_pick_can_exist_without_main_override(self):
        with patch.dict(os.environ, {'ANTHROPIC_BASE_URL': f'http://127.0.0.1:{self.cc.ADAPTER_PORT}/official'}), patch('sys.stdout', new_callable=io.StringIO):
            self.cc.helper_command(['tools', 'openai-oauth', 'gpt-6-luna', 'low'])
        self.assertNotIn('model', self.cc.load_routes()['launch-a'])
        with patch.dict(os.environ, {'ANTHROPIC_BASE_URL': 'https://api.anthropic.com'}), self.assertRaises(SystemExit):
            self.cc.helper_command(['auto', 'openai-oauth', 'gpt-6-luna'])

    def test_helper_pick_rejects_invalid_model_and_effort(self):
        with patch.dict(os.environ, {'ANTHROPIC_BASE_URL': f'http://127.0.0.1:{self.cc.ADAPTER_PORT}/official'}):
            for words in (['auto', 'openai-oauth', '../bad'], ['tools', 'openai-oauth', 'gpt-6-luna', 'nonsense']):
                with self.subTest(words=words), self.assertRaises(SystemExit):
                    self.cc.helper_command(words)
        self.assertEqual(self.cc.load_routes(), {})

    def test_relay_pending_blocks_stop_once(self):
        job = "12345678-1234-1234-1234-123456789abc"
        Path(self.cc.JOBS_DIR, job + ".out").write_text("", encoding="utf-8")
        transcript = Path(self.tmp.name, "agent.jsonl")
        transcript.write_text("CC_RELAY_PENDING " + job + "\n", encoding="utf-8")
        data = {"agent_type": "cc-sub", "agent_transcript_path": str(transcript)}
        first = self.cc.subagent_stop(data)
        self.assertEqual(first.get("decision"), "block")
        self.assertIn("agent-wait " + job, first["reason"])
        self.assertEqual(self.cc.subagent_stop(data), {})


class EffortSourceTests(unittest.TestCase):
    """The reasoning levels each provider publishes for one model."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cc = load_switch()
        self.cc.EFFORTS_FILE = str(Path(self.tmp.name) / "efforts.json")

    def tearDown(self):
        self.tmp.cleanup()

    def test_codex_model_cache_lists_an_openai_models_own_levels(self):
        cache = {"models": [{"slug": "gpt-x", "supported_reasoning_levels": [{"effort": "low"}, {"effort": "high"}]},
                           {"slug": "gpt-y", "supported_reasoning_levels": [{"effort": "max"}]}]}
        Path(self.tmp.name, "models_cache.json").write_text(json.dumps(cache), encoding="utf-8")
        with patch.dict(os.environ, {"CODEX_HOME": self.tmp.name}):
            self.assertEqual(self.cc.model_efforts("openai-oauth", "GPT-X"), ["low", "high"])
            self.assertIsNone(self.cc.model_efforts("openai-oauth", "gpt-z"))
            self.assertIsNone(self.cc.model_efforts("nvidia", "gpt-x"))

    def test_commandcode_levels_are_cached_on_disk_for_the_status_line(self):
        rows = [{"id": "a", "reasoningEfforts": ["low", "high"]}, {"id": "b"}]
        with patch.object(self.cc, "commandcode_catalog", return_value=rows):
            self.assertEqual(self.cc.model_efforts("commandcode", "a"), ["low", "high"])
            self.assertEqual(self.cc.model_efforts("commandcode-cli", "b"), [])
            self.assertIsNone(self.cc.model_efforts("commandcode", "c"))
        self.assertEqual(json.loads(Path(self.cc.EFFORTS_FILE).read_text(encoding="utf-8"))["models"],
                         {"a": ["low", "high"], "b": []})
        with patch.object(self.cc, "commandcode_catalog", side_effect=AssertionError("no network")):
            self.assertEqual(self.cc.model_efforts("commandcode", "a"), ["low", "high"])
            self.assertIsNone(self.cc.model_efforts("commandcode", "c"))


if __name__ == "__main__":
    unittest.main()
