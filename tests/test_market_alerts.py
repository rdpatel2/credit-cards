from copy import deepcopy
from datetime import datetime,timedelta,timezone
import pytest
from card_compass.market_alerts import initial_alerts,rule_values,evaluate,requirements,validate_alerts,is_quiet


NOW=datetime(2026,10,12,15,tzinfo=timezone.utc)


def price_rule():return rule_values('My price level','price',symbol='AAPL',direction='above',threshold='100')
def quote(price=110,**flags):return dict(price=price,currency='USD',source='Test provider',asOf=NOW.isoformat(),**flags)


def test_price_alert_latches_rearms_and_retains_public_provenance():
    alerts=initial_alerts();alerts['rules']=[price_rule()]
    before=deepcopy(alerts)
    candidate,new=evaluate(alerts,lambda *args:quote(),[],NOW)
    assert alerts==before and len(new)==1 and not new[0]['read']
    assert new[0]['source']=='Test provider' and 'not an instruction to trade' in new[0]['details']
    same,new=evaluate(candidate,lambda *args:quote(),[],NOW+timedelta(minutes=1))
    assert not new and len(same['events'])==1
    reset,new=evaluate(same,lambda *args:quote(99),[],NOW+timedelta(minutes=2))
    assert not new and not reset['rules'][0]['runtime']['active']
    crossed,new=evaluate(reset,lambda *args:quote(100),[],NOW+timedelta(minutes=3))
    assert len(new)==1 and len(crossed['events'])==2


@pytest.mark.parametrize('data',[None,quote(demo=True),quote(stale=True),dict(quote(),currency='EUR'),quote(float('nan'))])
def test_unusable_quotes_cannot_trigger_or_rearm(data):
    alerts=initial_alerts();alerts['rules']=[price_rule()]
    candidate,new=evaluate(alerts,lambda *args:data,[],NOW)
    assert not new
    active,_=evaluate(alerts,lambda *args:quote(),[],NOW)
    candidate,new=evaluate(active,lambda *args:data,[],NOW+timedelta(minutes=1))
    assert not new and candidate['rules'][0]['runtime']['active']


def test_earnings_windows_skip_closed_sessions_and_deduplicate_events():
    alerts=initial_alerts();alerts['rules']=[rule_values('Earnings reminder','earnings',symbol='AAPL',lookahead=7)]
    keys=requirements(alerts,NOW)
    assert ('earnings','2026-10-12') in keys and ('earnings','2026-10-17') not in keys
    provider=lambda kind,day:dict(items=[dict(symbol='AAPL',reportTime='After market close')],source='Test calendar',asOf=NOW.isoformat()) if day=='2026-10-14' else None
    candidate,new=evaluate(alerts,provider,[],NOW)
    assert len(new)==1 and '2026-10-14' in new[0]['title']
    repeated,new=evaluate(candidate,provider,[],NOW+timedelta(minutes=10))
    assert not new and len(repeated['events'])==1
    bad=lambda kind,day:dict(provider(kind,day),demo=True) if provider(kind,day) else None
    assert not evaluate(alerts,bad,[],NOW)[1]


def test_stale_feed_grace_and_recovery_rearm():
    alerts=initial_alerts();alerts['rules']=[rule_values('Watch data quality','stale',delayMinutes=5)]
    feeds=[dict(kind='quote',key='AAPL|1d',status='stale',error='Provider unavailable')]
    candidate,new=evaluate(alerts,lambda *args:None,feeds,NOW)
    assert not new and candidate['rules'][0]['runtime']['since']==NOW.isoformat()
    candidate,new=evaluate(candidate,lambda *args:None,feeds,NOW+timedelta(minutes=5))
    assert len(new)==1 and 'Provider unavailable' in new[0]['details']
    healthy=[dict(feeds[0],status='ready',error='')]
    candidate,new=evaluate(candidate,lambda *args:None,healthy,NOW+timedelta(minutes=6))
    assert not candidate['rules'][0]['runtime']['active']
    candidate,_=evaluate(candidate,lambda *args:None,feeds,NOW+timedelta(minutes=7))
    candidate,new=evaluate(candidate,lambda *args:None,feeds,NOW+timedelta(minutes=12))
    assert len(new)==1


def test_capacity_never_silently_deletes_notifications():
    alerts=initial_alerts();alerts['rules']=[price_rule()]
    seed,event=evaluate(alerts,lambda *args:quote(),[],NOW)
    import uuid
    alerts['events']=[dict(event[0],id=uuid.uuid4().hex) for _ in range(1000)]
    result,new=evaluate(alerts,lambda *args:quote(),[],NOW)
    assert not new and result['capacityWarning'] and len(result['events'])==1000
    assert not result['rules'][0]['runtime']['active']


def test_disabled_rules_quiet_hours_and_invalid_configuration():
    alerts=initial_alerts();rule=price_rule();rule['enabled']=False;alerts['rules']=[rule]
    assert not requirements(alerts,NOW) and not evaluate(alerts,lambda *args:quote(),[],NOW)[1]
    local_hour=NOW.astimezone().hour
    alerts['quietHours']=dict(start=local_hour,end=(local_hour+1)%24)
    assert is_quiet(alerts,NOW)
    broken=deepcopy(alerts);broken['quietHours']['end']=broken['quietHours']['start']
    with pytest.raises(ValueError,match='Quiet hours'):validate_alerts(broken)
