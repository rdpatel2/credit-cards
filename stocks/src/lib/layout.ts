import type { LayoutItem } from "./workspace";

export function overlaps(a: LayoutItem, b: LayoutItem) {
  return (
    a.i !== b.i &&
    a.x < b.x + b.w &&
    a.x + a.w > b.x &&
    a.y < b.y + b.h &&
    a.y + a.h > b.y
  );
}
function distance(a: LayoutItem, b: LayoutItem) {
  return Math.abs(a.x - b.x) + Math.abs(a.y - b.y);
}
function nearestFree(item: LayoutItem, occupied: LayoutItem[]): LayoutItem {
  // A nearest legal rectangle lies either at its requested row or an obstacle edge.
  const rows = new Set([
    0,
    item.y,
    ...occupied.flatMap((o) => [o.y - item.h, o.y + o.h]),
  ]);
  const candidates: LayoutItem[] = [];
  for (const y of rows) {
    if (y < 0 || y > 10000) continue;
    for (let x = 0; x <= 12 - item.w; x++) {
      const candidate = { ...item, x, y };
      if (!occupied.some((other) => overlaps(candidate, other)))
        candidates.push(candidate);
    }
  }
  candidates.sort(
    (a, b) =>
      distance(a, item) - distance(b, item) ||
      Math.abs(a.x - item.x) - Math.abs(b.x - item.x) ||
      a.y - b.y ||
      a.x - b.x,
  );
  return candidates[0] ?? item;
}
export function settleLayout(layout: LayoutItem[]): LayoutItem[] {
  const placed: LayoutItem[] = [];
  const displaced: LayoutItem[] = [];
  for (const item of layout) {
    if (placed.some((other) => overlaps(item, other))) displaced.push(item);
    else placed.push(item);
  }
  for (const item of displaced) placed.push(nearestFree(item, placed));
  return layout.map((item) => placed.find((next) => next.i === item.i)!);
}
export function resolveLayoutMove(
  previous: LayoutItem[],
  requested: LayoutItem,
  kind: "move" | "resize" = "move",
): LayoutItem[] {
  const original = previous.find((item) => item.i === requested.i);
  if (!original) return previous;
  const target = {
    ...requested,
    x: Math.max(0, Math.min(12 - requested.w, requested.x)),
    y: Math.max(0, Math.min(10000, requested.y)),
  };
  const others = previous.filter((item) => item.i !== target.i);
  const collided = others.filter((item) => overlaps(target, item));
  const replace = (items: LayoutItem[]) =>
    previous.map((item) => items.find((next) => next.i === item.i) ?? item);
  if (!collided.length) return replace([target]);

  // A deliberate drop at a neighbor's origin can swap into the vacated space.
  if (
    kind === "move" &&
    collided.length === 1 &&
    target.x === collided[0].x &&
    target.y === collided[0].y
  ) {
    const swapped = { ...collided[0], x: original.x, y: original.y };
    if (
      swapped.x + swapped.w <= 12 &&
      !overlaps(swapped, target) &&
      !others.some((item) => item.i !== swapped.i && overlaps(item, swapped))
    )
      return replace([target, swapped]);
  }

  // Reserve every unaffected widget. Only actual collisions can be adjusted.
  const occupied = [
    target,
    ...others.filter((item) => !collided.includes(item)),
  ];
  const adjusted = collided.map((item) => {
    const next = nearestFree(item, occupied);
    occupied.push(next);
    return next;
  });
  if (kind === "move") {
    const snapped = nearestFree(target, others);
    const neighborCost = adjusted.reduce(
      (sum, item, index) => sum + distance(item, collided[index]),
      0,
    );
    // Prefer a one-cell snap over displacing a tall neighbor many rows.
    if (distance(snapped, target) <= neighborCost) return replace([snapped]);
  }
  return replace([target, ...adjusted]);
}
export function reconcileLayout(previous: LayoutItem[], next: LayoutItem[]) {
  const changed = next.filter((item) => {
    const old = previous.find((o) => o.i === item.i);
    return (
      old &&
      ["x", "y", "w", "h"].some(
        (key) => item[key as keyof LayoutItem] !== old[key as keyof LayoutItem],
      )
    );
  });
  if (previous.length === next.length && changed.length === 1) {
    const old = previous.find((item) => item.i === changed[0].i)!;
    return resolveLayoutMove(
      previous,
      changed[0],
      old.w !== changed[0].w || old.h !== changed[0].h ? "resize" : "move",
    );
  }
  return settleLayout(next);
}
