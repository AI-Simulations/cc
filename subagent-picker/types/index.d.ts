export type Row = { value: string; label?: string }
// One column of /model's grid: `at` is its cursor, `pick` the row Enter sets (Space moves it to the cursor;
// absent, the cursor's), `current` the value in use (ticked), `note` shown with no rows. `all` is the
// column's unfiltered list: the Model column is a filtered, family-sorted view of it.
export type Col = { title: string; rows: Row[]; at: number; pick?: number; current?: string; note?: string; all?: Row[] }
export type GridTab = { name: 'Model' | 'Subagent' | 'Auto' | 'Tools' | 'Fallback' | 'Presets'; now: string; cols: Col[] }
// The whole /model menu; `room` is the list rows each column draws.
// `tick` changes only to redraw the cursor cell's scrolling name. `query` filters the model list by
// name, `sort` orders it by provider listing or by model family.
export type Grid = { tab: number; col: number; tabs: GridTab[]; room: number; status?: string; tick?: number; query?: string; sort?: 'family' }

declare module 'claude-code' {
  interface PluginState {
    'subagent-picker': { grid: Grid | null; cost: string }
  }
}
