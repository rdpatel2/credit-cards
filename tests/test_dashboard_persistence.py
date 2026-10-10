import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from copy import deepcopy
import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from card_compass.investments import InvestmentStore, validate_workspace
from card_compass.dashboard.layout_store import LayoutStore, snapshot
from card_compass.dashboard.layout_engine import ENGINE, intersects
from card_compass.dashboard.schema import normalize_dashboard
from tests.test_investments import trade


@pytest.fixture
def store(tmp_path):
    app=QApplication.instance() or QApplication([])
    backend=InvestmentStore(tmp_path);history=LayoutStore(backend,validate_workspace)
    yield history
    history.flush();history.deleteLater();app.processEvents()


def test_debounced_saving_failure_retry_preserves_optimistic_state(store,monkeypatch):
    backend=store.backend;save=backend.save;calls=[]
    def fail(state):calls.append(deepcopy(state));raise OSError('Disk unavailable')
    monkeypatch.setattr(backend,'save',fail)
    store.mutate('Rename',lambda state:state['pages'][0].__setitem__('name','My overview'))
    store.mutate('Rename again',lambda state:state['pages'][0].__setitem__('name','New overview'))
    assert store.status=='Saving…' and backend.state['pages'][0]['name']=='New overview' and not calls
    QTest.qWait(650)
    assert len(calls)==1 and store.status=='Failed – retry' and store.dirty
    assert not backend.path.exists() and backend.state['pages'][0]['name']=='New overview'
    monkeypatch.setattr(backend,'save',save);assert store.flush()
    assert store.status=='Saved' and InvestmentStore(backend.path.parent).state==backend.state


def test_exact_undo_redo_config_geometry_and_branch_preserves_new_trades(store):
    before=snapshot(store.backend.state);ident=store.backend.state['pages'][0]['widgets'][0]['id']
    def edit(state):
        p=state['pages'][0];p['settings']['compact']=False
        p['layouts']['lg']=ENGINE.move(p['layouts']['lg'],ident,dict(x=3,y=91,w=5,h=36),False)
        p['widgets'][0]['config']['symbol']='AMD';p['widgets'][0]['title']='My block'
    store.mutate('Configure and arrange',edit);after=snapshot(store.backend.state)
    store.backend.trade('MSFT',trade('buy',2,200))
    store.undo();assert snapshot(store.backend.state)==before and len(store.backend.state['trades']['MSFT'])==1
    store.redo();assert snapshot(store.backend.state)==after
    store.undo();store.mutate('New branch',lambda state:state['pages'][0].__setitem__('name','Branch'))
    assert not store.future


def test_layouts_remain_independent_per_breakpoint_and_dashboard(store):
    before=deepcopy(store.backend.state['pages']);ident=before[0]['widgets'][0]['id']
    def edit(state):
        dashboard=state['pages'][0];dashboard['settings']['compact']=False
        dashboard['layouts']['md']=ENGINE.move(dashboard['layouts']['md'],ident,dict(x=4,y=76),False)
    store.mutate('Arrange md',edit);store.flush()
    saved=InvestmentStore(store.backend.path.parent).state['pages']
    assert saved[0]['layouts']['md']!=before[0]['layouts']['md']
    assert all(saved[0]['layouts'][key]==before[0]['layouts'][key] for key in ('lg','sm','xs'))
    assert saved[1:]==before[1:]
    assert all(g['x']==0 and g['w']==12 for g in saved[0]['layouts']['xs'])


def test_loaded_layout_repairs_bounds_collisions_duplicates_and_missing_ids():
    raw=dict(id='bad',name='Repair',widgets=[dict(id='a',type='notes',config={}),dict(id='b',type='external',config={'keep':7})],
        layouts={'lg':[dict(i='a',x=-8,y=-1,w=40,h=1),dict(i='b',x=0,y=0,w=6,h=30),dict(i='missing',x=0,y=0,w=6,h=30),dict(i='a',x=6,y=0,w=6,h=30)]},
        settings={'gap':'nonsense'})
    value=normalize_dashboard(raw);layout=value['layouts']['lg']
    assert len(layout)==2 and {g['i'] for g in layout}=={'a','b'}
    assert all(0<=g['x'] and g['x']+g['w']<=12 and g['h']>=g['minH'] for g in layout)
    assert not intersects(*layout) and value['widgets'][1]['config']=={'keep':7}
    assert normalize_dashboard(value)==value


def test_first_free_insertion_uses_open_column_and_split_respects_locks_and_minima():
    left=dict(i='a',x=0,y=0,w=6,h=30)
    free=ENGINE.first_free([left],dict(i='b',w=6,h=30));assert free['x']==6 and free['y']==0
    blocks=[dict(i='fixed',x=0,y=0,w=3,h=60,locked=True),dict(i='target',x=3,y=0,w=3,h=30),dict(i='source',x=6,y=0,w=6,h=30,minW=6)]
    layout=ENGINE.split(blocks,'source','target','left')
    assert layout[0]['x']==0 and layout[0]['y']==0 and layout[0]['w']==3
    assert not any(intersects(a,b) for i,a in enumerate(layout) for b in layout[i+1:])
    blocks=[dict(i='a',x=0,y=0,w=9,h=30,minW=9),dict(i='b',x=0,y=30,w=9,h=30,minW=9)]
    layout=ENGINE.split(blocks,'b','a','left')
    assert all(g['w']>=g['minW'] for g in layout) and not intersects(*layout)
