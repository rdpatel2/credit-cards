from copy import deepcopy
from datetime import datetime,timezone
from decimal import Decimal
import math
import uuid
import pytest
from card_compass.investments import initial_workspace
from card_compass.performance import save_record
from card_compass.risk_analysis import monthly_series,risk_summary,correlations,parse_factors,validate_factor_set,month_end
from card_compass.finance import shift_month


def series(rates,start='2022-01'):
    return dict(start=month_end(shift_month(start,-1)),end=month_end(shift_month(start,len(rates)-1)),
        periods=[dict(period=shift_month(start,i),returnRate=Decimal(str(rate))) for i,rate in enumerate(rates)],sources=['Synthetic recorded return fixture'])


def factors(rows):
    return dict(version=1,id=uuid.uuid4().hex,frequency='monthly',currency='USD',name='Synthetic factors',
        source='Synthetic controlled fixture',file='synthetic.csv',importedAt=datetime.now(timezone.utc).isoformat(),rows=rows)


def test_exact_month_end_twr_separates_deposit_from_return_and_missing_flow_value_stops():
    state=initial_workspace()
    state=save_record(state,'legacy','2024-12-31',1000,'Synthetic statement')
    state=save_record(state,'legacy','2025-01-31',1600,'Synthetic statement')
    state=save_record(state,'legacy','2025-01-31',500,'Synthetic transfer',kind='contribution')
    state=save_record(state,'legacy','2025-02-28',1760,'Synthetic statement')
    result=monthly_series(state,'legacy','2025-01','2025-02')
    assert result['periods'][0]['returnRate']==Decimal('.1')
    assert result['periods'][1]['returnRate']==Decimal('.1')
    changed=save_record(state,'legacy','2025-02-15',100,'Synthetic transfer',kind='contribution')
    with pytest.raises(ValueError,match='every external-flow date'):monthly_series(changed,'legacy','2025-01','2025-02')
    changed=deepcopy(state);changed['performanceRecords']['valuations']=changed['performanceRecords']['valuations'][:-1]
    with pytest.raises(ValueError,match='exact opening and closing'):monthly_series(changed,'legacy','2025-01','2025-02')


def test_drawdown_from_linked_returns_sample_volatility_and_explicit_rf():
    result=risk_summary(series([.1,-.2]+[0]*10),annual_rf=0)
    assert result['cumulative']==Decimal('-.12') and result['maxDrawdown']==Decimal('-.2')
    expected_variance=(.05-.01/12)/11
    assert result['annualVolatility']==pytest.approx(math.sqrt(expected_variance*12))
    assert result['sharpe']==pytest.approx((-.1/12)/math.sqrt(expected_variance)*math.sqrt(12))
    assert 'user-entered' in result['sharpeSource']
    assert risk_summary(series([.1]*12))['sharpe'] is None
    assert risk_summary(series([0]*12),annual_rf=0)['sharpe'] is None
    assert risk_summary(series([.1]*3))['annualVolatility'] is None
    with pytest.raises(ValueError):risk_summary(series([float('nan')]))
    with pytest.raises(ValueError,match='below -100'):risk_summary(series([-1.1]*12))


def test_three_factor_regression_recovers_coefficients_and_rejects_collinear_inputs():
    rows=[];returns=[]
    for i in range(36):
        market=.02*math.sin(i);smb=.01*math.cos(i*1.3);hml=.015*math.sin(i*.7);rf=.001
        rows.append(dict(period=shift_month('2022-01',i),market_excess=str(market),smb=str(smb),hml=str(hml),rf=str(rf)))
        returns.append(rf+.002+1.2*market-.3*smb+.7*hml)
    result=risk_summary(series(returns),factors=factors(rows))
    model=result['factorModel']
    assert model['interceptMonthly']==pytest.approx(.002)
    assert model['marketBeta']==pytest.approx(1.2) and model['smb']==pytest.approx(-.3) and model['hml']==pytest.approx(.7)
    assert model['rSquared']==pytest.approx(1) and result['sharpe'] is not None
    changed=deepcopy(rows)
    for row in changed:row['smb']=row['market_excess']
    result=risk_summary(series(returns),factors=factors(changed))
    assert result['factorModel'] is None and 'collinear' in result['factorError']


def test_missing_factor_months_are_not_dropped_or_treated_as_zero():
    rows=[dict(period=shift_month('2022-01',i),market_excess='.01',smb='.01',hml='.01',rf='.001') for i in range(11)]
    result=risk_summary(series([.01]*12),factors=factors(rows))
    assert 'Missing factor periods' in result['factorError'] and result['sharpe'] is None
    result=risk_summary(series([.01]*11),factors=factors(rows))
    assert '36 aligned' in result['factorError'] and result['factorModel'] is None


def test_csv_percent_units_duplicate_month_and_known_missing_sentinel(tmp_path):
    path=tmp_path/'factors.csv'
    path.write_text('period,market_excess_percent,rf_percent,smb_percent,hml_percent\n2025-01,2,0.1,-1,3\n')
    result=parse_factors(path,'Fixture','Synthetic source')
    assert result['rows'][0]['market_excess']=='0.02' and result['rows'][0]['rf']=='0.001'
    assert result['file']=='factors.csv'
    changed=deepcopy(result);changed['rows'].append(changed['rows'][0])
    with pytest.raises(ValueError,match='unique'):validate_factor_set(changed)
    path.write_text('period,market_excess_percent,rf_percent,smb_percent,hml_percent\n2025-01,-99.99,0.1,-1,3\n')
    with pytest.raises(ValueError,match='sentinel'):parse_factors(path,'Fixture','Synthetic source')


def test_account_correlations_use_identical_periods_and_zero_variance_is_unavailable():
    x=[.02*math.sin(i) for i in range(12)]
    result=correlations(dict(A=series(x),B=series([-value for value in x]),Cash=series([0]*12)))
    assert result['matrix']['A']['B']==pytest.approx(-1)
    assert result['matrix']['A']['A']==pytest.approx(1) and result['matrix']['Cash']['A'] is None
    with pytest.raises(ValueError,match='match exactly'):correlations(dict(A=series(x),B=series(x,start='2022-02')))


def test_factor_solver_failure_preserves_basic_risk_results(monkeypatch):
    from scipy.linalg import LinAlgError
    def fail(*args,**kwargs):raise LinAlgError('fixture failed fit')
    monkeypatch.setattr('scipy.linalg.lstsq',fail)
    rows=[dict(period=shift_month('2022-01',i),market_excess='.01',smb='.01',hml='.01',rf='.001') for i in range(36)]
    result=risk_summary(series([.01]*36),factors=factors(rows))
    assert result['factorModel'] is None and 'did not converge' in result['factorError']
    assert result['maxDrawdown']==0 and result['cumulative']>0
