import { expect, test } from 'claude-code/testing'

import { costColor } from './register'

test('the mode row draws its hint and the session cost on one row', async ($, on) => {
  on('state.get', (_, e) => ({ value: e.key === 'cost' ? { value: '≈ $24.53 (7d $1,360/$13.6k Ultra)', version: 1 } : undefined }))
  const ui = await $.ui.mount({ plugin: 'subagent-picker', component: 'PromptHint', surface: 'terminal',
                                props: { isDraft: false, isWorking: false, hint: '⏵⏵ auto mode on · (shift+tab to cycle) · /tasks to see subagents' } })
  expect((await ui.find({ type: 'Text', text: /auto mode on/ }))?.text).toBe('⏵⏵ auto mode on · /tasks to see subagents · ≈ $24.53 (7d $1,360/$13.6k Ultra)')
  expect((await ui.find({ type: 'Text', text: /^\$\d/ }))?.text).toBe('$24.53')
  const week = await ui.find({ type: 'Text', text: /^7d / })
  expect(week?.text).toBe('7d $1,360/$13.6k')
  expect(week?.props.color).toBe(costColor(10))  // 10% of the week used
  await ui.unmount()
})

test('the shift+tab reminder is dropped before any cost is known', async ($, on) => {
  on('state.get', (_, e) => ({ value: e.key === 'cost' ? { value: '', version: 1 } : undefined }))
  const ui = await $.ui.mount({ plugin: 'subagent-picker', component: 'PromptHint', surface: 'terminal',
                                props: { isDraft: false, isWorking: false, hint: '⏵⏵ auto mode on (shift+tab to cycle)' } })
  expect((await ui.find({ type: 'Text', text: /auto mode on/ }))?.text).toBe('⏵⏵ auto mode on')
  await ui.unmount()
})

test('cost colour runs green to red, capped at $100', async () => {
  expect(costColor(0)).toBe('#5ce65c')    // statusline rgb(120): green
  expect(costColor(50)).toBe('#e6e65c')   // yellow
  expect(costColor(100)).toBe('#e65c5c')  // red
  expect(costColor(500)).toBe(costColor(100))
})
