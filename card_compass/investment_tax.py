"""Folio's limited 2026 federal / NC sale estimator, ported without rate changes."""
import math
from .investments import number

STATUSES = {'single':'Single','joint':'Married filing jointly','separate':'Married filing separately','head':'Head of household'}
DEDUCTIONS = {'single':16100,'joint':32200,'separate':16100,'head':24150}
NC_DEDUCTIONS = {'single':12750,'joint':25500,'separate':12750,'head':19125}
ORDINARY = {'single':[12400,50400,105700,201775,256225,640600],
            'joint':[24800,100800,211400,403550,512450,768700],
            'separate':[12400,50400,105700,201775,256225,384350],
            'head':[17700,67450,105700,201750,256200,640600]}
CAPITAL = {'single':[49450,545500],'joint':[98900,613700],'separate':[49450,306850],'head':[66200,579600]}
FIELDS = {'ordinaryIncome':'Ordinary income before deductions','preferentialIncome':'Existing long-term gains / qualified dividends',
          'deduction':'Federal deduction','stateDeduction':'NC deduction','magi':'Modified adjusted gross income',
          'investmentIncome':'Existing net investment income'}


def validate_profile(profile):
    if not isinstance(profile,dict) or profile.get('year')!=2026 or profile.get('state')!='NC' or profile.get('filingStatus') not in STATUSES:
        raise ValueError('Tax estimates support 2026 and North Carolina only.')
    for field in FIELDS:
        if type(profile[field]) not in (int, float):
            raise ValueError('Tax assumptions must be numeric.')
        number(profile[field])
    return profile


def progressive(income, thresholds, rates):
    start=total=0
    for end,rate in zip([*thresholds,float('inf')],rates):
        total += max(0,min(income,end)-start)*rate
        start=end
    return total


def estimate_tax(profile, short_gain, long_gain):
    p=validate_profile(profile)
    st,lt=float(short_gain),float(long_gain)
    if not math.isfinite(st) or not math.isfinite(lt):
        raise ValueError('Invalid gain.')
    if (st<0<lt) or (lt<0<st):
        net=st+lt
        st,lt=(max(0,net),min(0,net)) if st>0 else (min(0,net),max(0,net))
    st,lt=max(0,st),max(0,lt)
    status=p['filingStatus']
    def ordinary(value):
        return progressive(value,ORDINARY[status],[.1,.12,.22,.24,.32,.35,.37])
    def regular(short,long):
        income=max(0,p['ordinaryIncome']+short-p['deduction'])
        pref=max(0,p['preferentialIncome']+long-max(0,p['deduction']-p['ordinaryIncome']-short))
        pref_tax=progressive(income+pref,CAPITAL[status],[0,.15,.2])-progressive(income,CAPITAL[status],[0,.15,.2])
        return min(ordinary(income)+pref_tax,ordinary(income+pref))
    fed=regular(st,lt)-regular(0,0)
    threshold=250000 if status=='joint' else 125000 if status=='separate' else 200000
    def niit(gain):
        return .038*min(p['investmentIncome']+gain,max(0,p['magi']+gain-threshold))
    nii=max(0,niit(st+lt)-niit(0))
    def state(gain):
        return max(0,p['ordinaryIncome']+p['preferentialIncome']+gain-p['stateDeduction'])*.0399
    nc=state(st+lt)-state(0)
    return dict(federal=fed+nii,niit=nii,state=nc,total=fed+nii+nc)
