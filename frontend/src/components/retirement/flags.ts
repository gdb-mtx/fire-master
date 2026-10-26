// Placement for milestone flags under a chart's x axis: width-aware rows so
// labels never overlap; a label near the right edge flips to the left of its tick.

export interface Flag {
  m: number;
  text: string;
  warn?: boolean;
}

export const ROW = 17;
const charWidth = 6.4; // 12px Source Sans 3, measured average

export interface Placed extends Flag { px: number; row: number; flip: boolean }

export function placeFlags(items: Flag[], xOf: (m: number) => number, xMax: number): Placed[] {
  const taken: { row: number; a: number; b: number }[] = [];
  return items.map((it) => {
    const px = xOf(it.m);
    const w = it.text.length * charWidth + 10;
    const flip = px + 7 + w > xMax;
    const a = flip ? px - 7 - w : px - 4;
    const b = flip ? px + 4 : px + 7 + w;
    let row = 0;
    while (taken.some((p) => p.row === row && !(b < p.a - 10 || a > p.b + 10))) row++;
    taken.push({ row, a, b });
    return { ...it, px, row, flip };
  });
}

export const stripHeight = (placed: Placed[]) => (placed.length ? (Math.max(...placed.map((p) => p.row)) + 1) * ROW + 6 : 0);
