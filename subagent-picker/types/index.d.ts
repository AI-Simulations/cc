export type Row = { value: string; label?: string }
// One column of /model's grid: `at` is its cursor, `pick` the row Enter sets (Space moves it to the cursor;
// absent, the cursor's), `current` the value in use (ticked), `note` shown with no rows.
export type Col = { title: string; rows: Row[]; at: number; pick?: number; current?: string; note?: string }
export type GridTab = { name: 'Model' | 'Subagent' | 'Fallback'; now: string; cols: Col[] }
// The whole /model menu; `room` is the list rows each column draws.
// `tick` changes only to redraw the cursor cell's scrolling name.
export type Grid = { tab: number; col: number; tabs: GridTab[]; room: number; status?: string; tick?: number }

declare module 'claude-code' {
  interface PluginState {
    'subagent-picker': { grid: Grid | null }
  }
}
