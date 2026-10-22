// CMX-242 (CMX-212 P30): the Wall's fill presets. Each is a column count plus a target row
// count that fills the viewport height: "N columns" (rows 1) makes each pane full height, the
// ×grids stack `rows` of panes in the same height; extra panes wrap below and scroll.
export type Preset = { cols: number; rows: number; label: string };

export const WALL_PRESETS: readonly Preset[] = [
  { cols: 1, rows: 1, label: '1 column (stack)' },
  { cols: 2, rows: 1, label: '2 columns' },
  { cols: 3, rows: 1, label: '3 columns' },
  { cols: 4, rows: 1, label: '4 columns' },
  { cols: 2, rows: 2, label: '2 × 2 grid' },
  { cols: 3, rows: 2, label: '3 × 2 grid' },
];
