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

    def test_agent_runs_in_process_through_the_adapter_and_relays_otherwise(self):
        root = Path(self.tmp.name)
        twin = root / "agents" / "cc-sub-live.md"
        twin.parent.mkdir()
        twin.write_text("twin", encoding="utf-8")
        os.utime(twin, (1, 1))  # written before the session began
        (root / "main.jsonl").write_text("", encoding="utf-8")
        call = {"tool_name": "Agent", "transcript_path": str(root / "main.jsonl"),
                "tool_input": {"subagent_type": "cc-sub", "prompt": "do it", "description": "d"}}
        with patch.dict(os.environ, {"ANTHROPIC_BASE_URL": f"http://127.0.0.1:{self.cc.ADAPTER_PORT}/official"}):
            live = self.cc.agent_hook(call)["hookSpecificOutput"]["updatedInput"]
            self.assertEqual((live["subagent_type"], live["prompt"]), ("cc-sub-live", "do it"))
            os.utime(twin)  # rewritten after the session began: it cannot know the type yet
            fresh = self.cc.agent_hook(call)["hookSpecificOutput"]["updatedInput"]
            self.assertEqual(fresh["subagent_type"], "cc-sub", "A session older than the twin relays")
        with patch.dict(os.environ, {"ANTHROPIC_BASE_URL": ""}):
            relayed = self.cc.agent_hook({**call, "tool_input": {**call["tool_input"], "subagent_type": "cc-sub-live"}})
        updated = relayed["hookSpecificOutput"]["updatedInput"]
        self.assertEqual(updated["subagent_type"], "cc-sub", "A direct session relays even a cc-*-live call")
        self.assertTrue(updated["prompt"].startswith(self.cc.CONTEXT_PREFIX))

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
        with patch.dict(os.environ, {"CC_MAIN_PROVIDER": "official"}), self.assertRaises(SystemExit):
            self.cc.fallback_command([])

    def test_route_override_set_and_clear(self):
        self.cc.set_route("launch-a", "gemini", "gemini-3.8-flash")
        self.assertEqual(self.cc.load_routes()["launch-a"]["model"], "gemini-3.8-flash")
        self.cc.set_route("launch-a")
        self.assertNotIn("launch-a", self.cc.load_routes())

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
