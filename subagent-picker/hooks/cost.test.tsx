import { expect, test } from 'claude-code/testing'

import { costColor } from './register'

test('the mode row draws its hint and the session cost on one row', async ($, on) => {
  on('state.get', (_, e) => ({ value: e.key === 'cost' ? { value: 'api $24.53 (main $21.79 99% · sub $2.74 94% cached)', version: 1 } : undefined }))
  const ui = await $.ui.mount({ plugin: 'subagent-picker', component: 'PromptHint', surface: 'terminal',
                                props: { isDraft: false, isWorking: false, hint: '⏵⏵ auto mode on' } })
  expect((await ui.find({ type: 'Text', text: /auto mode on/ }))?.text).toBe('⏵⏵ auto mode on · api ')
  expect((await ui.find({ type: 'Text', text: /^\$\d/ }))?.text).toBe('$24.53')
  await ui.unmount()
})

test('cost colour runs green to red, capped at $100', async () => {
  expect(costColor(0)).toBe('#5ce65c')    // statusline rgb(120): green
  expect(costColor(50)).toBe('#e6e65c')   // yellow
  expect(costColor(100)).toBe('#e65c5c')  // red
  expect(costColor(500)).toBe(costColor(100))
})
