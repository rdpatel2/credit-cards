"""Pure, replaceable 12-column geometry. No Qt or component dependencies."""
from copy import deepcopy
from typing import Protocol

COLUMNS = 12
ROW_UNIT = 10
BREAKPOINTS = ('lg', 'md', 'sm', 'xs')


def breakpoint(width):
    return 'lg' if width >= 1200 else 'md' if width >= 900 else 'sm' if width >= 600 else 'xs'


def intersects(a, b):
    return a['x'] < b['x'] + b['w'] and b['x'] < a['x'] + a['w'] and a['y'] < b['y'] + b['h'] and b['y'] < a['y'] + a['h']


def integer(value, default):
    try:
        return int(value) if not isinstance(value, bool) else default
    except (TypeError, ValueError, OverflowError):
        return default


def clamp(item):
    item = deepcopy(item)
    item['minW'] = max(1, min(12, integer(item.get('minW'), 3)))
    item['minH'] = max(10, min(200, integer(item.get('minH'), 18)))
    item['maxW'] = max(item['minW'], min(12, integer(item.get('maxW'), 12)))
    item['maxH'] = max(item['minH'], min(500, integer(item.get('maxH'), 120)))
    item['w'] = max(item['minW'], min(item['maxW'], integer(item.get('w'), 6)))
    item['h'] = max(item['minH'], min(item['maxH'], integer(item.get('h'), 30)))
    item['x'] = max(0, min(12 - item['w'], integer(item.get('x'), 0)))
    item['y'] = max(0, min(10000, integer(item.get('y'), 0)))
    item['locked'] = bool(item.get('locked', False))
    return item


class LayoutEngine(Protocol):
    def repair(self, layout, compact=True): ...
    def move(self, layout, ident, values, compact=True): ...
    def split(self, layout, ident, target, side, compact=True): ...
    def derive(self, layout, target): ...
    def insert(self, layout, item, compact=True): ...
    def first_free(self, layout, item): ...


class GridLayoutEngine:
    def repair(self, layout, compact=True):
        items = [clamp(g) for g in layout]
        placed = []
        # Locked panes are obstacles. Corrupt overlapping locks are repaired once.
        for item in sorted(items, key=lambda g: (not g['locked'], g['y'], g['x'], g['i'])):
            self._push(item, placed)
            placed.append(item)
        if compact:
            self._compact(items)
        return items

    @staticmethod
    def _push(item, placed):
        hits = [g for g in placed if intersects(item, g)]
        while hits:
            item['y'] = max(g['y'] + g['h'] for g in hits)
            if item['y'] > 10000:
                raise ValueError('Dashboard is full; choose a position nearer the top.')
            hits = [g for g in placed if intersects(item, g)]

    @staticmethod
    def _compact(items):
        placed = [g for g in items if g['locked']]
        for item in sorted((g for g in items if not g['locked']), key=lambda g: (g['y'], g['x'], g['i'])):
            old_y = item['y']
            item['y'] = 0
            GridLayoutEngine._push(item, placed)
            # Never move below the resolved position solely to compact.
            item['y'] = min(old_y, item['y'])
            placed.append(item)

    def move(self, layout, ident, values, compact=True):
        items = [clamp(g) for g in layout]
        anchor = next(g for g in items if g['i'] == ident)
        if anchor['locked']:
            return items
        anchor.update({k: v for k, v in values.items() if k in ('x', 'y', 'w', 'h')})
        anchor.update(clamp(anchor))
        placed = [g for g in items if g['locked']]
        self._push(anchor, placed)
        placed.append(anchor)
        for other in sorted((g for g in items if g is not anchor and not g['locked']), key=lambda g: (g['y'], g['x'], g['i'])):
            self._push(other, placed)
            placed.append(other)
        if compact:
            self._compact(items)
        return items

    def split(self, layout, ident, target, side, compact=True):
        items = [clamp(g) for g in layout]
        source = next(g for g in items if g['i'] == ident)
        neighbor = next(g for g in items if g['i'] == target)
        if source['locked'] or neighbor['locked']:
            return self.move(items, ident, dict(x=neighbor['x'], y=neighbor['y'] + neighbor['h']), compact)
        if source['minW'] + neighbor['minW'] > 12:
            return self.move(items, ident, dict(x=neighbor['x'],y=neighbor['y']+neighbor['h']),compact)
        total = min(12, max(neighbor['w'], source['minW'] + neighbor['minW']))
        start = min(neighbor['x'], 12 - total)
        source_w = max(source['minW'], min(total // 2, total - neighbor['minW']))
        neighbor_w = total - source_w
        source.update(x=start if side == 'left' else start + neighbor_w, y=neighbor['y'], w=source_w)
        neighbor.update(x=start + source_w if side == 'left' else start, w=neighbor_w)
        source.update(clamp(source));neighbor.update(clamp(neighbor))
        obstacles=[g for g in items if g['locked']]
        while any(intersects(member,obstacle) for member in (source,neighbor) for obstacle in obstacles):
            hits=[obstacle for obstacle in obstacles if any(intersects(member,obstacle) for member in (source,neighbor))]
            y=max(g['y']+g['h'] for g in hits)
            source['y']=neighbor['y']=y
        # Both split members form the anchor; other blocks flow around them.
        placed = [g for g in items if g['locked']] + [source, neighbor]
        for other in sorted((g for g in items if g not in placed), key=lambda g: (g['y'], g['x'], g['i'])):
            self._push(other, placed)
            placed.append(other)
        if compact:
            self._compact(items)
        return items

    def insert(self, layout, item, compact=True):
        items = self.repair(layout, compact)
        item = clamp(item)
        self._push(item, items)
        return self.repair([*items, item], compact)

    def first_free(self,layout,item):
        candidate=clamp(item)
        for y in sorted({0,*[g['y']+g['h'] for g in layout]}):
            for x in range(13-candidate['w']):
                candidate.update(x=x,y=y)
                if not any(intersects(candidate,g) for g in layout):return candidate
        raise ValueError('Dashboard is full.')

    def derive(self, layout, target):
        items = deepcopy(layout)
        if target == 'xs':
            y = 0
            for item in sorted(items, key=lambda g: (g['y'], g['x'])):
                item.update(x=0, y=y, w=12, maxW=12)
                y += item['h']
        return self.repair(items, True)


ENGINE = GridLayoutEngine()


def dashboard_layout(dashboard, size):
    layouts = dashboard.setdefault('layouts', {})
    if size not in layouts:
        source = next((layouts[key] for key in BREAKPOINTS if key in layouts), [])
        layouts[size] = ENGINE.derive(source, size)
    return layouts[size]
