import { expect, test } from 'claude-code/testing'

import { effortBar, parseModels, parseProfiles } from './register'

test('parses agents.json profiles with their fallback', async () => {
  const out = JSON.stringify({
    sub: { provider: 'commandcode-cli', model: 'xiaomi/mimo-v2.6-pro', effort: 'high' },
    big: { provider: 'anthropic', model: 'claude-opus-5-5', effort: 'max', autocompact: 80, fallback: { provider: 'nvidia', model: 'a/b', effort: 'low' } },
  })
  expect(parseProfiles(out).map(p => p.name)).toEqual(['sub', 'big'])
  expect(parseProfiles(out)[0]).toEqual({ name: 'sub', provider: 'commandcode-cli', model: 'xiaomi/mimo-v2.6-pro', effort: 'high', autocompact: 'default' })
  expect(parseProfiles(out)[1]!.fallback).toEqual({ provider: 'nvidia', model: 'a/b', effort: 'low' })
  expect(parseProfiles('not json')).toEqual([])
})

test('model rows drop the current-model star', async () => {
  expect(parseModels('sub: a/b, effort high, autocompact default\n  * xiaomi/mimo\n    gpt-6-luna')).toEqual(['xiaomi/mimo', 'gpt-6-luna'])
})

test('effort bar fills one dot per level', async () => {
  expect(effortBar('low')).toBe('●○○○○')
  expect(effortBar('xhigh')).toBe('●●●●○')
  expect(effortBar('max')).toBe('●●●●●')
  expect(effortBar('default')).toBe('◌◌◌◌◌')
})

import { parseTarget } from './register'

test('helper status preserves explicit picks and distinguishes inheritance', async () => {
  expect(parseTarget('Helper: inherit session provider', 'Helper')).toBe(undefined)
  expect(parseTarget('Helper: openai-oauth/gpt-6-luna\nEffort: low', 'Helper')).toEqual({ provider: 'openai-oauth', model: 'gpt-6-luna', effort: 'low' })
})

test('route and fallback status keep slashes in the model id and read the effort line', async () => {
  expect(parseTarget('Fallback: off', 'Fallback')).toBe(undefined)
  expect(parseTarget('Route: launch default', 'Route')).toBe(undefined)
  expect(parseTarget('Fallback: nvidia/deepseek-ai/deepseek-v4-pro', 'Fallback')).toEqual({ provider: 'nvidia', model: 'deepseek-ai/deepseek-v4-pro' })
  expect(parseTarget('Route: gemini/models/gemini-3.8-flash\nEffort: high', 'Route')).toEqual({ provider: 'gemini', model: 'models/gemini-3.8-flash', effort: 'high' })
})

import { colTop, effortOptions, gridLayout, RING_KEYS } from './register'

test('effort rows follow the model ladder and flag its default on that level', async () => {
  expect(effortBar('off', ['off', 'low', 'high', 'max'])).toBe('○○○')
  expect(effortBar('high', ['off', 'low', 'high', 'max'])).toBe('●●○')
  const rows = effortOptions(['off', 'low', 'high', 'max'], 'high')
  expect(rows.map(r => r.value)).toEqual(['default', 'off', 'low', 'high', 'max'])
  expect(rows[3]!.label).toBe('high ●●○ def')
  expect(effortOptions(['low', 'medium'], 'xhigh').map(r => r.value)).toEqual(['default', 'low', 'medium'])
})

test('a grid column keeps its cursor centred within the list', async () => {
  expect(colTop(0, 69, 10)).toBe(0)
  expect(colTop(30, 69, 10)).toBe(25)
  expect(colTop(68, 69, 10)).toBe(59)
  expect(colTop(2, 3, 10)).toBe(0) // short list
})

test('the ring targets sit around the cursor cell: one each way, the tab ends on the tab line', async () => {
  const col = (title: string, n: number, at: number) => ({ title, at, rows: Array.from({ length: n }, (_, i) => ({ value: `${title}${i}` })) })
  const g = { tab: 0, col: 1, room: 5, tabs: [{ name: 'Model' as const, now: 'now', cols: [col('Provider', 3, 1), col('Model', 20, 10), col('Effort', 4, 2)] }, { name: 'Fallback' as const, now: '', cols: [] }] }
  const L = gridLayout(g, 100, 5)
  expect(L.c.y).toBe(4 + 10 - colTop(10, 20, 5))
  expect([L.u.x, L.u.y]).toEqual([L.c.x, L.c.y - 1])
  expect([L.d.x, L.d.y]).toEqual([L.c.x, L.c.y + 1])
  expect(L.l.x).toBeLessThan(L.c.x)
  expect(L.r.x).toBeGreaterThan(L.c.x)
  expect(L.c.label).toContain('❯ Model10')
  expect(L.u.label).toContain('Model9')
  expect(L.lines[0]!.text).toContain('[ Main model ]')
  expect(L.find.y).toBe(2)
  expect(L.filter.x).toBeGreaterThan(L.find.x)
  expect(L.sort.x).toBeGreaterThan(L.filter.x)
  expect(L.sort.label).toBe('sort: listed')
  expect(gridLayout({ ...g, sort: 'family' }, 100, 5).sort.label).toBe('sort: family')
  // the first row of a column: ↑ lands on its title; the first column: ← lands on the blank margin
  const top = gridLayout({ ...g, col: 0, tabs: [{ ...g.tabs[0]!, cols: [col('Provider', 3, 0), col('Model', 20, 10), col('Effort', 4, 2)] }, g.tabs[1]!] }, 100, 5)
  expect(top.u.y).toBe(3)
  expect([top.l.x, top.l.label]).toEqual([0, ' '])
  expect(Object.values(RING_KEYS).map(k => k[0]).sort()).toEqual(['down', 'find', 'left', 'right', 'sort', 'tab', 'tab', 'up'])
})

test('a pick away from the cursor keeps its mark, and narrow columns leave the Compact column on screen', async () => {
  const rows = (...v: string[]) => v.map(value => ({ value }))
  const tab = { name: 'Model' as const, now: '', cols: [
    { title: 'Provider', rows: rows('official', 'opencode-go'), at: 1, pick: 0 },
    { title: 'Model', rows: rows('claude-opus-5-5'), at: 0 },
    { title: 'Effort', rows: rows('default', 'high'), at: 0 },
    { title: 'Compact', rows: [{ value: 'default', label: 'def' }, { value: '95', label: '95%' }], at: 0, current: 'default' },
  ] }
  const L = gridLayout({ tab: 0, col: 0, room: 2, tabs: [tab] }, 60, 2)
  expect(L.lines[4]!.text).toContain('❯ opencode-go')
  expect(L.lines.some(line => line.text.includes('Compact: def ✔'))).toBe(true)
  expect(L.lines[4]!.text.trimEnd().length).toBeLessThanOrEqual(60)
  const wide = gridLayout({ tab: 0, col: 0, room: 2, tabs: [tab] }, 120, 2)
  expect(wide.lines[4]!.text).toContain('› official')
  expect(wide.lines[5]!.text).toContain('❯ opencode-go')
})

import { marqueeText } from './register'

test('a name wider than its column scrolls at once, pausing at the end', async () => {
  expect(marqueeText('abc', 5, 9)).toBe('abc  ')
  expect(marqueeText('abcdefg', 5, 0)).toBe('abcde')
  expect(marqueeText('abcdefg', 5, 1)).toBe('bcdef') // moving from the first step
  expect(marqueeText('abcdefg', 5, 2)).toBe('cdefg') // scrolled to the end
  expect(marqueeText('abcdefg', 5, 5)).toBe('cdefg') // paused there
  expect(marqueeText('abcdefg', 5, 6)).toBe('abcde') // and round again
})

import { familySort, filterRows, modelFamily, parsePresets, presetTarget, shapeModels } from './register'

test('a model id maps to its family', async () => {
  expect(modelFamily('claude-opus-5-5')).toBe('Claude')
  expect(modelFamily('anthropic/claude-sonnet-5-5')).toBe('Claude')
  expect(modelFamily('gpt-6-sol')).toBe('GPT')
  expect(modelFamily('openai/gpt-6.1-sol')).toBe('GPT')
  expect(modelFamily('o3-mini')).toBe('GPT')
  expect(modelFamily('deepseek-ai/deepseek-v4.1-flash')).toBe('DeepSeek')
  expect(modelFamily('z-ai/glm-5.3')).toBe('GLM')
  expect(modelFamily('qwen3.8-max')).toBe('Qwen')
  expect(modelFamily('MiniMax-M3')).toBe('MiniMax')
  expect(modelFamily('xiaomi/mimo-v2.6-pro')).toBe('MiMo')
  expect(modelFamily('gemini-3.8-flash')).toBe('Gemini')
  expect(modelFamily('mistralai/mistral-large')).toBe('Mistral')
  expect(modelFamily('some-new-model-9')).toBe('Some')
  expect(modelFamily('')).toBe('Other')
})

test('family order groups a family together, alphabetical inside it', async () => {
  const rows = ['qwen3.8-max', 'gpt-6-sol', 'claude-opus-5-5', 'deepseek/d', 'gpt-6-luna'].map(value => ({ value }))
  expect(familySort(rows).map(r => r.value)).toEqual(['claude-opus-5-5', 'deepseek/d', 'gpt-6-luna', 'gpt-6-sol', 'qwen3.8-max'])
})

test('the search reads the name shown and the id, case-insensitively', async () => {
  const rows = [
    { value: 'deepseek/deepseek-v4.1-flash', label: 'DeepSeek V4.1 Flash' },
    { value: 'claude-opus-5-5' },
  ]
  expect(filterRows(rows, 'FLASH').map(r => r.value)).toEqual(['deepseek/deepseek-v4.1-flash'])
  expect(filterRows(rows, 'opus').map(r => r.value)).toEqual(['claude-opus-5-5'])
  expect(filterRows(rows, '  ')).toEqual(rows)
  expect(filterRows(rows, 'zzz')).toEqual([])
  expect(shapeModels(rows, 'deepseek', 'family').map(r => r.value)).toEqual(['deepseek/deepseek-v4.1-flash'])
})

test('preset rows carry their provider, model and effort', async () => {
  const rows = parsePresets([
    'official\tclaude-opus-5-5\t\tofficial · claude-opus-5-5',
    'openai-oauth\tgpt-6.1-terra\tlow\topenai-oauth · gpt-6.1-terra · low',
    'broken',
  ].join('\n'))
  expect(rows.map(r => r.label)).toEqual(['official · claude-opus-5-5', 'openai-oauth · gpt-6.1-terra · low'])
  expect(presetTarget(rows[0]!.value)).toEqual({ provider: 'official', model: 'claude-opus-5-5' })
  expect(presetTarget(rows[1]!.value)).toEqual({ provider: 'openai-oauth', model: 'gpt-6.1-terra', effort: 'low' })
  expect(presetTarget('not json')).toBe(undefined)
})

import { relaunchCommand } from './register'

test('a direct session relaunches into the same conversation on the picked route', async () => {
  expect(relaunchCommand('nvidia', 'a/b', 'default', 'sid-1')).toBe('ccx nvidia a/b --resume sid-1')
  expect(relaunchCommand('openai-oauth', 'gpt-6.1-sol', 'high', 'sid-1')).toBe('ccx openai-oauth gpt-6.1-sol --effort high --resume sid-1')
  // Claude's --effort ignores levels outside its own ladder; leave them off rather than lose them silently
  expect(relaunchCommand('openai-oauth', 'gpt-6.1-sol', 'ultra', 'sid-1')).toBe('ccx openai-oauth gpt-6.1-sol --resume sid-1')
})
