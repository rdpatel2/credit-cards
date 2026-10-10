"""Versioned dashboard normalization and loss-preserving v1 migration."""
from copy import deepcopy
import uuid
from .layout_engine import ENGINE, BREAKPOINTS, clamp

VERSION = 2


def normalize_dashboard(raw, legacy=False):
    value = deepcopy(raw)
    value.setdefault('id', uuid.uuid4().hex)
    value.setdefault('name', 'Dashboard')
    defaults = dict(compact=True, density='comfortable', gap=12, reducedMotion=False)
    defaults.update(value.get('settings', {}))
    defaults['gap'] = max(4, min(24, int(defaults.get('gap', 12))))
    defaults['compact'] = bool(defaults['compact'])
    value['settings'] = defaults
    ids = set()
    widgets = []
    for widget in value.get('widgets', [])[:30]:
        widget = deepcopy(widget)
        ident = str(widget.get('id') or uuid.uuid4().hex)
        if ident in ids:
            ident = uuid.uuid4().hex
        ids.add(ident)
        config = widget.get('config')
        if config is None:
            config = {k: v for k, v in widget.items() if k not in ('id', 'type', 'title')}
        widgets.append(dict(id=ident, type=str(widget.get('type', 'unknown')), title=str(widget.get('title', widget.get('type', 'Unknown widget')))[:80], config=deepcopy(config)))
    value['widgets'] = widgets
    layouts = value.get('layouts', {})
    if legacy or not layouts:
        source = value.pop('layout', [])
        layouts = {'lg': [dict(g, y=round(g.get('y', 0) * 3.2), h=max(18, round(g.get('h', 9) * 3.2))) for g in source]}
    normalized = {}
    for key, layout in layouts.items():
        if key not in BREAKPOINTS:
            continue
        seen = set()
        result = []
        for geometry in layout:
            ident = geometry.get('i', geometry.get('id'))
            if ident in ids and ident not in seen:
                result.append(clamp(dict(geometry, i=ident)))
                seen.add(ident)
        for ident in (w['id'] for w in widgets if w['id'] not in seen):
            result = ENGINE.insert(result, dict(i=ident, x=0, y=0, w=6, h=30), defaults['compact'])
        normalized[key] = ENGINE.repair(result, defaults['compact'])
    source = next(iter(normalized.values()), [])
    for key in BREAKPOINTS:
        if key not in normalized:
            normalized[key] = ENGINE.derive(source, key)
    value['layouts'] = normalized
    value.pop('layout', None)
    return value


def migrate_workspace(raw):
    value = deepcopy(raw)
    version = value.get('version', 1)
    if version not in (1, 2):
        raise ValueError('Unsupported investment workspace version.')
    value['version'] = VERSION
    value['pages'] = [normalize_dashboard(p, version == 1) for p in value['pages']]
    value['stockTemplate'] = normalize_dashboard(value['stockTemplate'], version == 1)
    for key in ('stockOverrides', 'newsPages'):
        value[key] = {ident: normalize_dashboard(p, version == 1) for ident, p in value.get(key, {}).items()}
    value.setdefault('templates', {})
    return value
