"""Limited gain-only US sale scenarios, with explicit year/jurisdiction coverage."""
import math
import re
from .investments import number

STATUSES = {'single':'Single','joint':'Married filing jointly','separate':'Married filing separately','head':'Head of household'}
DEDUCTIONS = {'single':16100,'joint':32200,'separate':16100,'head':24150}
NC_DEDUCTIONS = {'single':12750,'joint':25500,'separate':12750,'head':19125}
ORDINARY = {'single':[12400,50400,105700,201775,256225,640600],
            'joint':[24800,100800,211400,403550,512450,768700],
            'separate':[12400,50400,105700,201775,256225,384350],
            'head':[17700,67450,105700,201750,256200,640600]}
CAPITAL = {'single':[49450,545500],'joint':[98900,613700],'separate':[49450,306850],'head':[66200,579600]}
DEDUCTIONS_BY_YEAR = {2025: {'single':15750,'joint':31500,'separate':15750,'head':23625}, 2026: DEDUCTIONS}
ORDINARY_BY_YEAR = {2025: {'single':[11925,48475,103350,197300,250525,626350],
    'joint':[23850,96950,206700,394600,501050,751600],
    'separate':[11925,48475,103350,197300,250525,375800],
    'head':[17000,64850,103350,197300,250500,626350]}, 2026: ORDINARY}
CAPITAL_BY_YEAR = {2025: {'single':[48350,533400],'joint':[96700,600050],
    'separate':[48350,300000],'head':[64750,566700]}, 2026: CAPITAL}
NC_RATES = {2025:.0425,2026:.0399}
SOURCES = {
    2025:'https://www.irs.gov/irb/2024-45_IRB',
    2026:'https://www.irs.gov/pub/irs-drop/rp-25-32.pdf',
    'deductions':'https://www.irs.gov/pub/irs-drop/rp-25-32.pdf',
    'NC':'https://www.ncdor.gov/taxes-forms/individual-income-tax/tax-rate-schedules',
    'NIIT':'https://www.irs.gov/taxtopics/tc559',
}
FIELDS = {'ordinaryIncome':'Ordinary income before deductions','preferentialIncome':'Existing long-term gains / qualified dividends',
          'deduction':'Federal deduction','stateDeduction':'NC deduction','magi':'Modified adjusted gross income',
          'investmentIncome':'Existing net investment income'}


def validate_profile(profile):
    if not isinstance(profile,dict) or type(profile.get('year')) is not int or not 1900<=profile['year']<=2200 or profile.get('filingStatus') not in STATUSES:
        raise ValueError('Choose a valid tax year and filing status.')
    if not isinstance(profile.get('state'),str) or not 1<=len(profile['state'])<=40 or not isinstance(profile.get('country','US'),str) or not re.fullmatch('[A-Z]{2}',profile.get('country','US')):
        raise ValueError('Identify a jurisdiction and two-letter country code.')
    for field in ('residentUS','fullYearNC'):
        if field in profile and type(profile[field]) is not bool:raise ValueError('Confirm the tax-residency assumptions explicitly.')
    for field in FIELDS:
        if type(profile.get(field)) not in (int, float):
            raise ValueError('Tax assumptions must be numeric.')
        number(profile[field])
    return profile


def coverage(profile):
    p=validate_profile(profile)
    if p.get('country','US')!='US' or not p.get('residentUS',True):
        return 'Estimates support US resident individuals only; this jurisdiction/residency is outside coverage.'
    if p['year'] not in ORDINARY_BY_YEAR:
        return 'No reviewed tax-rate table for this year. Recorded-data review remains available.'
    if p['state'] not in ('NC','FEDERAL'):
        return 'No reviewed state-rate model for this jurisdiction. Select federal-only to exclude state taxes explicitly.'
    if p['state']=='NC' and not p.get('fullYearNC',True):
        return 'NC estimates require full-year NC residency; part-year/nonresident allocation is outside coverage.'
    return ''


def progressive(income, thresholds, rates):
    start=total=0
    for end,rate in zip([*thresholds,float('inf')],rates):
        total += max(0,min(income,end)-start)*rate
        start=end
    return total


def estimate_tax(profile, short_gain, long_gain):
    p=validate_profile(profile)
    reason=coverage(p)
    if reason:raise ValueError(reason)
    st,lt=float(short_gain),float(long_gain)
    if not math.isfinite(st) or not math.isfinite(lt):
        raise ValueError('Invalid gain.')
    if st<0 or lt<0:
        raise ValueError('Loss scenarios require separate review; this estimator supports non-negative gains only.')
    status=p['filingStatus']
    year=p['year']
    def ordinary(value):
        return progressive(value,ORDINARY_BY_YEAR[year][status],[.1,.12,.22,.24,.32,.35,.37])
    def regular(short,long):
        income=max(0,p['ordinaryIncome']+short-p['deduction'])
        pref=max(0,p['preferentialIncome']+long-max(0,p['deduction']-p['ordinaryIncome']-short))
        pref_tax=progressive(income+pref,CAPITAL_BY_YEAR[year][status],[0,.15,.2])-progressive(income,CAPITAL_BY_YEAR[year][status],[0,.15,.2])
        return min(ordinary(income)+pref_tax,ordinary(income+pref))
    fed=regular(st,lt)-regular(0,0)
    threshold=250000 if status=='joint' else 125000 if status=='separate' else 200000
    def niit(gain):
        return .038*min(p['investmentIncome']+gain,max(0,p['magi']+gain-threshold))
    nii=max(0,niit(st+lt)-niit(0))
    def state(gain):
        return max(0,p['ordinaryIncome']+p['preferentialIncome']+gain-p['stateDeduction'])*NC_RATES[year]
    nc=state(st+lt)-state(0) if p['state']=='NC' else None
    return dict(federal=fed+nii,niit=nii,state=nc,total=fed+nii+(nc or 0),stateCovered=nc is not None,
                source=SOURCES[year],rateReviewed='2026-10-10')
