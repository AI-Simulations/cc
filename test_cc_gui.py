"""Offline checks for the Tkinter picker's pure parts: no window, no network, no cc run."""
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('cc_gui', root / 'cc-gui.py')
gui = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gui)

# Families group one provider's list by category, the same way the terminal grid's `s` does.
assert gui.model_family('claude-opus-5-5') == 'Claude'
assert gui.model_family('anthropic/claude-sonnet-5-5') == 'Claude'
assert gui.model_family('gpt-6-sol') == 'GPT'
assert gui.model_family('openai/gpt-6.1-sol') == 'GPT'
assert gui.model_family('deepseek-ai/deepseek-v4.1-flash') == 'DeepSeek'
assert gui.model_family('deepseek/d') == 'DeepSeek'
assert gui.model_family('z-ai/glm-5.3') == 'GLM'
assert gui.model_family('qwen3.8-max') == 'Qwen'
assert gui.model_family('MiniMax-M3') == 'MiniMax'
assert gui.model_family('xiaomi/mimo-v2.6-pro') == 'MiMo'
assert gui.model_family('some-new-model-9') == 'Some'
assert gui.model_family('') == 'Other'

assert gui.family_sorted(['qwen3.8-max', 'gpt-6-sol', 'claude-opus-5-5', 'deepseek/d', 'gpt-6-luna']) == [
    'claude-opus-5-5', 'deepseek/d', 'gpt-6-luna', 'gpt-6-sol', 'qwen3.8-max']
assert gui.filtered(['deepseek-v4.1-flash', 'claude-opus-5-5'], 'FLASH') == ['deepseek-v4.1-flash']
assert gui.filtered(['deepseek-v4.1-flash'], '  ') == ['deepseek-v4.1-flash']
assert gui.filtered(['deepseek-v4.1-flash'], 'zzz') == []

# The facts the launch menu shows, and its sort order: best first, rows missing facts last.
assert gui.tokens(1048576) == '1.05M'  # the launch menu's own rounding
assert gui.tokens(131072) == '131K'
assert gui.facts_text({'ctx': 1000000, 'in': 4, 'out': 20, 'aa': 45, 'score': 72}) == 'ctx 1M · $4/$20 · AA 45 · → 72'
assert gui.facts_text({'ctx': None, 'in': None, 'out': None, 'aa': None}) == 'ctx ? · $? · AA ?'
assert gui.facts_text({'ctx': 1000, 'in': 0, 'out': 0, 'aa': 1}) == 'ctx 1K · free · AA 1'
facts = {
    'cheap': {'ctx': 1000, 'in': 1, 'out': 2, 'aa': 10, 'score': 50, 'missing': 0},
    'big': {'ctx': 1000000, 'in': 5, 'out': 25, 'aa': 40, 'score': 90, 'missing': 0},
    'unknown': {'ctx': None, 'in': None, 'out': None, 'aa': None, 'score': 0, 'missing': 3},
}
models = ['cheap', 'unknown', 'big']
assert gui.sort_models(models, facts, 'context') == ['big', 'cheap', 'unknown']
assert gui.sort_models(models, facts, 'price') == ['cheap', 'big', 'unknown']
assert gui.sort_models(models, facts, 'intelligence') == ['big', 'cheap', 'unknown']
assert gui.sort_models(models, facts, 'score') == ['big', 'cheap', 'unknown']
assert gui.sort_models(models, facts, 'listed') == models
assert gui.effort_values([], 'high') == ['default', 'low', 'medium', 'high', 'xhigh', 'max']
assert gui.effort_values(['low', 'high'], 'max') == ['default', 'low', 'high', 'max']
assert gui.effort_values(None) == ['default', 'low', 'medium', 'high', 'xhigh', 'max']

# cc's picker rows and model listings.
assert gui.tab_rows('official\tClaude official\ncommandcode\tCommand Code\nnoise') == [
    ('official', 'Claude official'), ('commandcode', 'Command Code')]
assert gui.indented_rows('sub: a/b, effort high\n  * xiaomi/mimo\n    gpt-6-luna\n') == ['xiaomi/mimo', 'gpt-6-luna']
assert gui.plain_rows('claude-opus-5-5\n\n.deepseek/x\n(no models)\n') == ['claude-opus-5-5', '.deepseek/x']
assert gui.parse_target('Route: nvidia/deepseek-ai/x\nEffort: high', 'Route') == ['nvidia', 'deepseek-ai/x', 'high']
assert gui.parse_target('Route: launch default', 'Route') is None
assert gui.parse_target('Helper: inherit session provider', 'Helper') is None
assert gui.parse_target('Fallback: openai-oauth/gpt-6.1-sol', 'Fallback') == ['openai-oauth', 'gpt-6.1-sol']

assert gui.migrate_view({'sort': 'Family'})['sort'] == 'Intelligence'
assert gui.migrate_view({'sort': 'Family', 'v': gui.VIEW_VERSION})['sort'] == 'Family'
assert gui.migrate_view({'sort': 'Price'})['sort'] == 'Price'
assert gui.migrate_view(None) == {}

# One window per session, named safely after whatever identifies the launch.
os.environ['CC_LAUNCH_ID'] = 'abc/../def'
assert gui.lock_file().name == 'gui-abcdef.pid'
os.environ.pop('CC_LAUNCH_ID', None)
os.environ.pop('CLAUDE_CODE_SESSION_ID', None)
assert gui.lock_file().name == 'gui-default.pid'
assert gui.pid_alive(os.getpid())
assert not gui.pid_alive(0)
finished = subprocess.Popen([sys.executable, '-c', 'pass'],
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
finished.wait()
assert not gui.pid_alive(finished.pid), 'a reaped child still reads as alive'

print('cc-gui pure parts ok')

# Real Tk widgets, hidden; all cc commands and background work stay stubbed.
import unittest
from unittest import mock
import tempfile
from tkinter import ttk


class PickerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.cache_path = Path(self.temp.name) / 'gui-view.json'
        self.cache_patch = mock.patch.object(gui, 'VIEW_FILE', self.cache_path, create=True)
        self.cache_patch.start()
        self.calls = mock.patch.object(gui, 'run_cc', return_value=(True, 'Route: beta/model-b\nEffort: high'))
        self.run_cc = self.calls.start()
        self.bg = mock.patch.object(gui.App, 'background', autospec=True)
        self.background = self.bg.start()
        self.app = gui.App()
        self.app.withdraw()
        self.tab = self.app.tabs['model']
        self.tab.provider.set('beta')
        self.tab.models = ['model-a', 'model-b']
        self.tab.render_models('model-b')

    def tearDown(self):
        self.app.destroy()
        self.bg.stop()
        self.calls.stop()
        self.cache_patch.stop()
        self.temp.cleanup()

    def test_hover_restores_zebra_striping(self):
        self.tab.models = ['m0', 'm1', 'm2']
        self.tab.render_models('m2')
        row = self.tab.iids['m1']
        self.assertIn('zebra', self.tab.list.item(row, 'tags'))
        self.tab.hover.show(row)
        self.assertNotIn('zebra', self.tab.list.item(row, 'tags'))
        self.assertIn('hover', self.tab.list.item(row, 'tags'))
        self.tab.hover.clear()
        self.assertIn('zebra', self.tab.list.item(row, 'tags'))
        self.assertNotIn('hover', self.tab.list.item(row, 'tags'))

    def test_selecting_a_hovered_row_clears_the_hover_tag(self):
        self.tab.models = ['m0', 'm1', 'm2']
        self.tab.render_models('m2')
        row = self.tab.iids['m1']
        self.tab.hover.show(row)
        self.assertIn('hover', self.tab.list.item(row, 'tags'))
        self.tab.list.selection_set(row)
        self.tab.on_select()
        self.assertNotIn('hover', self.tab.list.item(row, 'tags'))
        self.assertIn('zebra', self.tab.list.item(row, 'tags'))

    def test_row_index_counts_family_headers(self):
        self.tab.models = ['claude-opus-5-5', 'gpt-6-sol', 'qwen3.8-max']
        self.tab.sort_mode.set('Family')
        self.tab.render_models()
        deep = self.tab.iids['qwen3.8-max']
        self.assertEqual(self.tab.list.index(deep), 0)  # within its family only
        self.assertEqual(self.tab.row_index(deep), 5)    # two headers above it

    def test_deep_family_pick_is_revealed_by_row(self):
        self.tab.models = ([f'claude-x{i}' for i in range(8)] + [f'gpt-x{i}' for i in range(8)]
                           + [f'qwen-x{i}' for i in range(8)])
        self.tab.sort_mode.set('Family')
        self.tab.render_models('qwen-x7')
        deep, top = self.tab.iids['qwen-x7'], self.tab.iids['claude-x0']
        with mock.patch.object(self.tab.list, 'see') as see, \
                mock.patch.object(self.tab.list, 'yview_moveto') as move:
            self.tab.reveal(deep)
        see.assert_called_once_with(deep)
        move.assert_not_called()
        with mock.patch.object(self.tab.list, 'see') as see, \
                mock.patch.object(self.tab.list, 'yview_moveto') as move:
            self.tab.reveal(top)
        move.assert_called_once_with(0)
        see.assert_not_called()

    def test_current_moved_onto_a_hovered_row_survives(self):
        self.tab.models = ['m0', 'm1', 'm2']
        self.tab.current = ['beta', 'm0', 'high']
        self.tab.render_models('m0')
        row = self.tab.iids['m1']  # odd index: zebra
        self.tab.hover.show(row)
        self.tab.current = ['beta', 'm1', 'high']
        self.tab.mark_current()
        self.tab.hover.clear()
        tags = self.tab.list.item(row, 'tags')
        self.assertIn('current', tags)
        self.assertIn('zebra', tags)
        self.assertNotIn('hover', tags)

    def test_current_tag_is_created_before_zebra(self):
        calls = []
        original = ttk.Treeview.tag_configure

        def record(tree, tag, **options):
            calls.append(tag)
            return original(tree, tag, **options)

        with mock.patch.object(ttk.Treeview, 'tag_configure', record):
            gui.PickerTab(self.app, 'model', self.app.container)
        self.assertLess(calls.index('current'), calls.index('zebra'))

    def test_tab_hover_marks_the_tab_under_the_pointer(self):
        self.app.hover_tab('subagent', True)
        self.assertEqual(self.app.tab_labels['subagent'].cget('bg'), gui.HOVER)
        self.app.hover_tab('subagent', False)
        self.assertEqual(self.app.tab_labels['subagent'].cget('bg'), gui.BG)
        self.app.hover_tab('model', True)  # the selected tab keeps its raised panel
        self.assertEqual(self.app.tab_labels['model'].cget('bg'), gui.PANEL)

    def test_default_sort_is_intelligence(self):
        self.assertEqual(self.tab.sort_mode.get(), 'Intelligence')
        self.tab.facts = {'low': {'aa': 10}, 'high': {'aa': 90}, 'none': {}}
        self.tab.models = ['low', 'none', 'high']
        self.tab.render_models()
        order = [model for model, iid in self.tab.iids.items() if iid in self.tab.list.get_children()]
        self.assertEqual(order, ['high', 'low', 'none'])

    def test_hover_highlights_a_row_and_skips_selected_and_current(self):
        self.tab.models = ['model-a', 'model-b']
        self.tab.render_models('model-b')
        hover = self.tab.hover
        a, b = self.tab.iids['model-a'], self.tab.iids['model-b']
        hover.show(b)  # the selected row is already marked by its selection
        self.assertNotIn('hover', self.tab.list.item(b, 'tags'))
        hover.show(a)
        self.assertIn('hover', self.tab.list.item(a, 'tags'))
        self.assertNotIn('hover', self.tab.list.item(b, 'tags'))
        hover.show('')
        self.assertNotIn('hover', self.tab.list.item(a, 'tags'))
        self.tab.current = ['beta', 'model-a', 'default']
        self.tab.render_models('model-a')
        hover.show(self.tab.iids['model-a'])
        self.assertNotIn('hover', self.tab.list.item(self.tab.iids['model-a'], 'tags'))

    def test_model_selection_survives_sort_search_and_facts_refresh(self):
        for sort, query in [('Listed', ''), ('Family', 'model'), ('Price', '')]:
            self.tab.sort_mode.set(sort)
            self.tab.query.set(query)
            self.tab.render_models()
            self.assertEqual(self.tab.chosen(), 'model-b')
        self.tab.show_facts('beta', (True, '{"id":"model-a","ctx":1000}'))
        self.assertEqual(self.tab.chosen(), 'model-b')

    def test_current_provider_and_effort_selected_on_open(self):
        self.tab.current = None
        self.tab.show_providers([('alpha', 'Alpha'), ('beta', 'Beta')])
        self.assertEqual(self.tab.provider.get(), 'beta')
        self.assertEqual(self.tab.effort.get(), 'high')

    def test_current_model_marked_without_changing_selection(self):
        self.tab.current = ['beta', 'model-a', 'high']
        self.tab.render_models('model-b')
        self.assertIn('current', self.tab.list.item(self.tab.iids['model-a'], 'tags'))
        self.assertEqual(self.tab.chosen(), 'model-b')

    def test_subagent_and_fallback_read_selected_profile(self):
        profiles = {'sub': {'provider': 'beta', 'model': 'model-b', 'effort': 'high',
                            'fallback': {'provider': 'alpha', 'model': 'backup', 'effort': 'low'}}}
        with mock.patch.object(gui, 'load_profiles', return_value=profiles):
            for key, expected in [('subagent', ['beta', 'model-b', 'high']),
                                  ('fallback', ['alpha', 'backup', 'low'])]:
                tab = self.app.tabs[key]
                tab.target.set('sub')
                tab.render_now()
                self.assertEqual(tab.current, expected)

    def test_provider_load_ignores_stale_model_response(self):
        self.tab.load_models()
        work, done = self.background.call_args_list[-2].args[1:]
        self.tab.provider.set('alpha')
        self.tab.load_models()
        done((True, 'stale-model'))
        self.assertNotIn('stale-model', self.tab.models)
        work()
        self.run_cc.assert_called_with('route', 'models', 'beta')

    def test_presets_keep_selection_and_search_model_and_effort(self):
        tab = self.app.tabs['presets']
        tab.show('alpha\tmodel-a\tlow\tFirst\n'
                 'beta\tmodel-b\thigh\tSecond\t1')
        tab.list.selection_set(tab.iids[1][0])
        tab.render()
        self.assertEqual(tab.picked()['provider'], 'beta')
        tab.query.set('model-b')
        self.assertEqual([p['provider'] for p in tab.shown], ['beta'])
        tab.query.set('high')
        self.assertEqual([p['provider'] for p in tab.shown], ['beta'])

    def test_presets_show_explicit_fields_and_current_route(self):
        tab = self.app.tabs['presets']
        tab.show('alpha\tmodel-a\tlow\tFirst\n'
                 'beta\tmodel-b\thigh\tSecond\t1')
        self.assertEqual(tab.picked()['provider'], 'beta')
        self.assertEqual(tab.list.item(tab.iids[1][0], 'values'),
                         ('model-b', 'high', 'Custom', 'Current'))
        self.assertIn('current', tab.list.item(tab.iids[1][0], 'tags'))

    def test_apply_captures_profile_before_worker_runs(self):
        for key in ('subagent', 'fallback'):
            tab = self.app.tabs[key]
            tab.target.set('first')
            tab.provider.set('beta')
            tab.models = ['model-b']
            tab.render_models('model-b')
            tab.apply()
            work = self.background.call_args.args[1]
            tab.target.set('second')
            work()
            args = self.run_cc.call_args.args
            self.assertIn('first', args)
            self.assertNotIn('second', args)

    def test_model_facts_have_separate_columns(self):
        self.tab.facts = {'model-b': {'ctx': 1000000, 'in': 4, 'out': 20, 'aa': 45, 'score': 72}}
        self.tab.render_models('model-b')
        self.assertEqual(self.tab.list.cget('columns'), ('context', 'price', 'intelligence', 'score'))
        self.assertEqual(self.tab.list.item(self.tab.iids['model-b'], 'values'), ('1M', '$4/$20', '45', '72'))

    def test_apply_updates_in_place_without_relisting(self):
        self.tab.apply()
        done = self.background.call_args.args[2]
        iids = dict(self.tab.iids)
        with mock.patch.object(self.tab, 'load_models') as reload, mock.patch.object(self.tab, 'render_models') as rebuild:
            done((True, 'Route: beta/model-b\nEffort: high'))
        reload.assert_not_called()
        rebuild.assert_not_called()
        self.assertEqual(self.tab.iids, iids)
        self.assertIn('current', self.tab.list.item(iids['model-b'], 'tags'))

    def test_presets_apply_updates_in_place(self):
        tab = self.app.tabs['presets']
        tab.show('beta\tmodel-b\thigh\tSecond\t1')
        tab.apply()
        done = self.background.call_args.args[2]
        iids = list(tab.iids)
        with mock.patch.object(tab, 'load') as reload, mock.patch.object(tab, 'render') as rebuild:
            done((True, 'Route: beta/model-b\nEffort: high'))
        reload.assert_not_called()
        rebuild.assert_not_called()
        self.assertEqual(tab.iids, iids)

    def test_view_cache_restores_browser_picks_without_applying(self):
        self.tab.sort_mode.set('Price')
        self.tab.query.set('model')
        self.tab.effort.set('low')
        self.tab.render_models('model-a')
        self.app.destroy()
        self.app = gui.App()
        self.app.withdraw()
        tab = self.app.tabs['model']
        tab.show_providers([('alpha', 'Alpha'), ('beta', 'Beta')])
        tab.show_models((True, 'model-a\nmodel-b'))
        self.assertEqual(tab.provider.get(), 'beta')
        self.assertEqual(tab.chosen(), 'model-a')
        self.assertEqual(tab.sort_mode.get(), 'Price')
        self.assertEqual(tab.query.get(), 'model')
        self.assertEqual(tab.effort.get(), 'low')
        self.assertFalse(any(c.args[:1] == ('route',) and len(c.args) > 1 for c in self.run_cc.call_args_list))

    def test_cached_models_avoid_relisting_provider(self):
        self.tab.load_models()
        self.background.call_args_list[-2].args[2]((True, 'model-a\nmodel-b'))
        with mock.patch.object(self.tab, 'show_models', wraps=self.tab.show_models) as show:
            self.tab.load_models()
        show.assert_called_once()

    def test_view_keeps_a_pick_per_provider(self):
        tab = self.tab
        tab.sort_mode.set('Listed')
        for provider, models, pick in [('alpha', ['model-a', 'model-b'], 'model-a'),
                                       ('beta', ['model-b', 'model-a'], 'model-b')]:
            tab.provider.set(provider)
            tab.models = models
            tab.render_models(pick)
        tab.provider.set('alpha')
        tab.show_models((True, 'model-a\nmodel-b'))
        self.assertEqual(tab.chosen(), 'model-a')
        tab.provider.set('beta')
        tab.show_models((True, 'model-b\nmodel-a'))
        self.assertEqual(tab.chosen(), 'model-b')

    def test_view_cache_prefers_the_saved_browsing_provider(self):
        self.tab.view = {'target': '', 'provider': 'alpha', 'picks': {'alpha': 'model-a'},
                         'sort': 'Listed', 'query': '', 'effort': 'default'}
        self.tab.show_providers([('beta', 'Beta'), ('alpha', 'Alpha')])
        self.assertEqual(self.tab.provider.get(), 'alpha')

    def test_view_cache_restores_the_saved_target(self):
        gui.save_view('subagent', {'target': 'second', 'provider': 'beta', 'picks': {'beta': 'model-b'},
                                   'sort': 'Listed', 'query': '', 'effort': 'default'})
        tab = self.app.tabs['subagent']
        tab.view = gui.session_view().get('subagent')
        with mock.patch.object(gui, 'load_profiles', return_value={'first': {}, 'second': {}}):
            tab.load()
        self.assertEqual(tab.target.get(), 'second')

    def test_subagent_apply_moves_current_mark_in_place(self):
        tab = self.app.tabs['subagent']
        tab.target.set('sub')
        tab.provider.set('beta')
        tab.sort_mode.set('Listed')
        tab.models = ['model-b']
        tab.render_models('model-b')
        tab.apply()
        done = self.background.call_args.args[2]
        profiles = {'sub': {'provider': 'beta', 'model': 'model-b', 'effort': 'default'}}
        with mock.patch.object(gui, 'load_profiles', return_value=profiles), \
                mock.patch.object(tab, 'load_models') as reload, \
                mock.patch.object(tab, 'render_models') as rebuild:
            done((True, 'sub: beta/model-b'))
        reload.assert_not_called()
        rebuild.assert_not_called()
        self.assertIn('current', tab.list.item(tab.iids['model-b'], 'tags'))

    def test_target_change_does_not_carry_selection(self):
        tab = self.app.tabs['subagent']
        tab.view = {'target': 'first', 'provider': 'alpha', 'picks': {'alpha': 'model-a'},
                    'sort': 'Listed', 'query': '', 'effort': 'default'}
        tab.target.set('first')
        tab.provider.set('alpha')
        tab.models = ['model-a', 'model-a2']
        tab.render_models('model-a')
        tab.target.set('second')
        profiles = {'first': {'provider': 'alpha', 'model': 'model-a'},
                    'second': {'provider': 'alpha', 'model': 'model-a2'}}
        with mock.patch.object(gui, 'load_profiles', return_value=profiles):
            tab.show_providers([('alpha', 'Alpha')])
        self.assertIsNone(tab.chosen())
        self.assertNotIn('model-a', tab.view.get('picks', {}).values())

    def test_target_change_clears_rows_before_any_redraw(self):
        tab = self.app.tabs['subagent']
        tab.view = {'target': 'first', 'provider': 'alpha', 'picks': {'alpha': 'model-a'},
                    'sort': 'Listed', 'query': '', 'effort': 'default'}
        tab.target.set('first')
        tab.provider.set('alpha')
        tab.sort_mode.set('Listed')
        tab.show_models((True, '  model-a\n  model-a2'))
        self.assertEqual(tab.chosen(), 'model-a')
        profiles = {'first': {'provider': 'alpha', 'model': 'model-a'},
                    'second': {'provider': 'alpha', 'model': 'model-a2'}}
        with mock.patch.object(gui, 'load_profiles', return_value=profiles):
            tab.target.set('second')
            tab.load()
            done = self.background.call_args.args[2]
            tab.render_models()  # a Find/Sort/facts redraw while the new list loads
            done([('alpha', 'Alpha')])
        self.assertIsNone(tab.chosen())
        self.assertNotIn('model-a', tab.view.get('picks', {}).values())

    def test_stale_provider_list_is_ignored(self):
        tab = self.app.tabs['fallback']
        profiles = {'first': {'provider': 'alpha', 'model': 'model-a', 'effort': 'default'}}
        with mock.patch.object(gui, 'load_profiles', return_value=profiles):
            tab.target.set('session')
            tab.load()
            first = self.background.call_args.args[2]
            tab.target.set('first')
            tab.load()
            second = self.background.call_args.args[2]
            second([('nvidia', 'NV')])
            first([('official', 'Official')])
        self.assertEqual([r[0] for r in tab.providers], ['nvidia'])

    def test_target_picker_change_is_kept(self):
        tab = self.app.tabs['subagent']
        tab.view = {'target': 'first', 'provider': 'alpha', 'picks': {'alpha': 'model-a'},
                    'sort': 'Listed', 'query': '', 'effort': 'default'}
        with mock.patch.object(gui, 'load_profiles', return_value={'first': {}, 'second': {}}):
            tab.load()
            self.assertEqual(tab.target.get(), 'first')
            tab.target.set('second')
            tab.load()
            self.assertEqual(tab.target.get(), 'second')

    def test_subagent_view_does_not_leak_across_targets(self):
        tab = self.app.tabs['subagent']
        tab.target.set('first')
        tab.provider.set('beta')
        tab.sort_mode.set('Listed')
        tab.models = ['model-a', 'model-b']
        tab.render_models('model-a')
        tab.target.set('second')
        tab.models = []
        tab.render_models()
        tab.show_models((True, '  model-b\n  model-a'))
        self.assertEqual(tab.chosen(), 'model-b')

    def test_target_tabs_explain_which_profile_they_edit(self):
        for key in ('subagent', 'fallback'):
            labels = [w.cget('text') for w in self.app.tabs[key].winfo_children() if isinstance(w, ttk.Label)]
            self.assertTrue(any('profile' in text for text in labels), labels)
        model = [w.cget('text') for w in self.app.tabs['model'].winfo_children() if isinstance(w, ttk.Label)]
        self.assertFalse(any('profile' in text for text in model), model)

    def make_editor(self):
        return gui.PresetEditor(self.app, {'provider': 'beta', 'model': 'first', 'effort': 'default'}, mock.Mock())

    def test_editor_has_five_editable_categories_and_saves_together(self):
        bundle = {key: {'provider': 'beta', 'model': key + '-model', 'effort': 'low'}
                  for key in ('model', 'subagent', 'fallback', 'auto', 'tools')}
        editor = gui.PresetEditor(self.app, {'provider': 'beta', 'model': 'model-model',
                                           'effort': 'low', 'settings': bundle}, mock.Mock())
        self.assertEqual(set(editor.fields), set(bundle))
        editor.fields['tools']['model'].set('changed-tools')
        editor.save()
        self.background.call_args.args[1]()
        args = self.run_cc.call_args.args
        self.assertEqual(args[:3], ('presets', 'set-bundle', 'beta'))
        import json
        saved = json.loads(args[3])
        self.assertEqual(saved['tools']['model'], 'changed-tools')
        self.assertEqual(saved['auto']['model'], 'auto-model')
        editor.close()

    def test_presets_apply_all_categories_and_stop_on_failure(self):
        tab = self.app.tabs['presets']
        bundle = {key: {'provider': 'beta', 'model': key + '-model', 'effort': 'low'}
                  for key in ('model', 'subagent', 'fallback', 'auto', 'tools')}
        preset = {'provider': 'beta', 'model': 'model-model', 'effort': 'low',
                  'label': 'Beta', 'settings': bundle}
        with mock.patch.object(tab, 'picked', return_value=preset), \
                mock.patch.object(gui, 'load_profiles', return_value={'first': {}, 'second': {}}):
            tab.apply()
            self.run_cc.reset_mock()
            work = self.background.call_args.args[1]
            work()
        self.assertEqual(self.run_cc.call_args_list, [
            mock.call('route', 'beta', 'model-model', 'low'),
            mock.call('agents', 'set', 'first', '--provider', 'beta', '--model', 'subagent-model', '--effort', 'low'),
            mock.call('agents', 'set', 'second', '--provider', 'beta', '--model', 'subagent-model', '--effort', 'low'),
            mock.call('fallback', 'beta', 'fallback-model', 'low'),
            mock.call('helper', 'auto', 'beta', 'auto-model', 'low'),
            mock.call('helper', 'tools', 'beta', 'tools-model', 'low')])
        self.run_cc.reset_mock()
        self.run_cc.side_effect = [(True, 'ok'), (False, 'blocked')]
        ok, out = work()
        self.assertFalse(ok)
        self.assertIn('Subagent', out)
        self.assertEqual(self.run_cc.call_count, 2)

    def test_editor_restricts_known_efforts_and_uses_actual_providers(self):
        self.run_cc.side_effect = lambda *args: (True, 'nvidia\tNVIDIA\ngpt4free\tFree' if args == ('fallback', 'routes')
                                                else 'commandcode\tCommand Code\ncommandcode-cli\tCommand Code CLI\ngpt4free\tFree\nnvidia\tNVIDIA')
        editor = self.make_editor()
        fields = editor.fields['tools']
        fields['effort'].set('max')
        editor.show_efforts(['low', 'high'], 'tools')
        self.assertEqual(fields['effort_box'].cget('values'), ('default', 'low', 'high'))
        self.assertEqual(fields['effort'].get(), 'default')
        editor.show_efforts([], 'tools')
        self.assertEqual(fields['effort_box'].cget('values'), ('default',))
        editor.show_efforts(None, 'tools')
        self.assertIn('high', fields['effort_box'].cget('values'))
        self.assertIn('commandcode-cli', editor.fields['subagent']['provider_box'].cget('values'))
        self.assertNotIn('commandcode-cli', editor.fields['fallback']['provider_box'].cget('values'))
        self.assertNotIn('beta', editor.fields['tools']['provider_box'].cget('values'))
        editor.close()

    def test_editor_model_dropdown_spinner_stops_on_success_failure_and_off(self):
        editor = self.make_editor()
        fields = editor.fields['model']
        self.assertTrue(fields['loading'])
        self.assertTrue(fields['spinner'].cget('text'))
        self.background.call_args.args[2]((False, 'unavailable'))
        self.assertFalse(fields['loading'])
        self.assertEqual(fields['spinner'].cget('text'), '')
        editor.load_models()
        self.assertTrue(fields['loading'])
        self.background.call_args.args[2]((True, 'first\nsecond'))
        self.assertFalse(fields['loading'])
        editor.fields['tools']['provider'].set('off')
        editor.load_models('tools')
        self.assertFalse(editor.fields['tools']['loading'])
        editor.close()

    def test_editor_ignores_stale_effort_response(self):
        editor = self.make_editor()
        editor.load_efforts()
        old_done = self.background.call_args.args[2]
        editor.model.set('second')
        editor.load_efforts()
        self.background.call_args.args[2](['low'])
        old_done(['max'])
        self.assertEqual(editor.effort_box.cget('values'), ('default', 'low'))
        editor.close()

    def test_editor_ignores_callbacks_after_close(self):
        editor = self.make_editor()
        models_done = self.background.call_args.args[2]
        editor.load_efforts()
        efforts_done = self.background.call_args.args[2]
        editor.close()
        models_done((True, 'first\nsecond'))
        efforts_done(['low'])


if __name__ == '__main__':
    unittest.main()



def test_model_compact_writes_claude_settings(tmp_path, monkeypatch):
    import json
    settings = tmp_path / 'settings.json'
    settings.write_text(json.dumps({'modelSettings': {'m': {'effortLevel': 'medium'}}}))
    monkeypatch.setattr(gui, 'SETTINGS_FILE', settings)
    assert gui.model_compact('m') == 'global'
    assert gui.set_model_compact('m', '200k')[0] and gui.model_compact('m') == '200k'
    assert json.loads(settings.read_text())['modelSettings']['m'] == {'effortLevel': 'medium', 'autoCompactWindow': 200000}
    assert gui.set_model_compact('m', 'global')[0] and gui.model_compact('m') == 'global'
    assert not gui.set_model_compact('m', '50%')[0] and not gui.set_model_compact('m', '20k')[0]
