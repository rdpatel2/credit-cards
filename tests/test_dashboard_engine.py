from copy import deepcopy
import random
from card_compass.dashboard.layout_engine import ENGINE, clamp, intersects, breakpoint


def block(ident, x=0, y=0, w=6, h=30, **values):
    return dict(i=ident, x=x, y=y, w=w, h=h, **values)


def valid(layout):
    assert all(0 <= g['x'] and g['x'] + g['w'] <= 12 and g['y'] >= 0 for g in layout)
    assert not any(intersects(a, b) for i, a in enumerate(layout) for b in layout[i + 1:])


def test_independent_column_compaction_closes_tall_neighbor_gap():
    layout = [block('analyst', h=20), block('news', x=6, h=60), block('earnings', y=60, h=25)]
    result = ENGINE.repair(layout)
    assert next(g for g in result if g['i'] == 'earnings')['y'] == 20
    valid(result)


def test_dragged_anchor_pushes_others_and_free_mode_preserves_position():
    layout = [block('a'), block('b', y=30), block('c', x=6)]
    result = ENGINE.move(layout, 'b', dict(x=0, y=0), False)
    assert next(g for g in result if g['i'] == 'b')['y'] == 0
    assert next(g for g in result if g['i'] == 'a')['y'] == 30
    valid(result)
    result = ENGINE.move(result, 'b', dict(x=3, y=125), False)
    assert next(g for g in result if g['i'] == 'b')['y'] == 125


def test_split_insert_bounds_resize_and_locks():
    result = ENGINE.split([block('a', w=12), block('b', y=30), block('c', y=60)], 'b', 'a', 'right')
    assert result[0]['x'] == 0 and result[1]['x'] == 6
    valid(result)
    result = ENGINE.move(result, 'b', dict(w=99, h=1, x=100, y=-50))
    assert result[1]['w'] == 12 and result[1]['h'] == 18
    valid(result)
    fixed = block('fixed', locked=True)
    result = ENGINE.move([fixed, block('b', y=30)], 'b', dict(x=0, y=0))
    assert result[0]['y'] == 0 and result[1]['y'] == 30
    valid(result)


def test_breakpoint_derivation_keeps_all_ids_and_single_column():
    assert [breakpoint(w) for w in (1200, 900, 600, 599)] == ['lg', 'md', 'sm', 'xs']
    result = ENGINE.derive([block('a'), block('b', x=6)], 'xs')
    assert len(result) == 2 and all(g['w'] == 12 and g['x'] == 0 for g in result)
    valid(result)


def test_random_collision_repair_is_deterministic_and_does_not_mutate_input():
    rng = random.Random(114)
    for _ in range(50):
        layout = [block(str(i), x=rng.randrange(-5, 15), y=rng.randrange(80), w=rng.randrange(2, 14), h=rng.randrange(15, 60)) for i in range(25)]
        original = deepcopy(layout)
        result = ENGINE.repair(layout)
        valid(result)
        assert layout == original and ENGINE.repair(layout) == result
