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
  expect(rows.map(r => r.value)).toEqual(['off', 'low', 'default', 'max'])
  expect(rows[2]!.label).toContain('(def)')
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
  // the first row of a column: ↑ lands on its title; the first column: ← lands on the blank margin
  const top = gridLayout({ ...g, col: 0, tabs: [{ ...g.tabs[0]!, cols: [col('Provider', 3, 0), col('Model', 20, 10), col('Effort', 4, 2)] }, g.tabs[1]!] }, 100, 5)
  expect(top.u.y).toBe(3)
  expect([top.l.x, top.l.label]).toEqual([0, ' '])
  expect(Object.values(RING_KEYS).map(k => k[0]).sort()).toEqual(['down', 'left', 'right', 'tab', 'tab', 'up'])
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
