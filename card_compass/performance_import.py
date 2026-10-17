"""Portable account valuation/funding CSVs with mapped, reviewable corrections."""
import csv
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
from .investments import number
from .performance import empty_records,validate_records
from .planning import valid_day


def parse_file(path):
    path=Path(path)
    if path.stat().st_size>10_000_000:raise ValueError('Import at most 10 MB at once.')
    with path.open(encoding='utf-8-sig',newline='') as handle:
        reader=csv.DictReader(handle)
        fields=reader.fieldnames or []
        required=['account_id','date','amount_usd','source']
        flow='kind' in fields or 'flow_id' in fields
        if flow:required+=['kind','flow_id']
        if len(fields)!=len(set(fields)) or any(key not in fields for key in required):
            raise ValueError('Required, unique columns: '+', '.join(required))
        rows=[]
        for line,row in enumerate(reader,2):
            try:
                if len(rows)>=10000:raise ValueError('Import at most 10,000 records at once.')
                if None in row or any(row.get(key) is None for key in required):raise ValueError('Incorrect number of CSV fields.')
                if 'schema_version' in row and row['schema_version']!='1':raise ValueError('Unsupported CSV schema version.')
                if 'dataset' in row and row['dataset']!=('funding_flows' if flow else 'valuations'):raise ValueError('Wrong dataset for these performance columns.')
                value={key:row[key].strip() for key in required}
                if not 1<=len(value['account_id'])<=120:raise ValueError('Identify the source account.')
                valid_day(value['date'],future=False)
                amount=number(value['amount_usd'])
                if amount!=amount.quantize(Decimal('.01')):raise ValueError('Use USD amounts with at most two decimal places.')
                if not 1<=len(value['source'])<=120:raise ValueError('Identify the source/reference (up to 120 characters).')
                if flow and (value['kind'] not in ('contribution','withdrawal') or amount<=0 or not 1<=len(value['flow_id'])<=80):
                    raise ValueError('Flows need a stable ID, positive amount, and contribution/withdrawal kind.')
                rows.append(dict(value,_line=line,_flow=flow))
            except ValueError as exc:raise ValueError(f'Line {line}: {exc}') from exc
    if not rows:raise ValueError('The file has no data rows.')
    return rows


def preview_import(state,rows,mapping):
    candidate=deepcopy(state)
    records=candidate.setdefault('performanceRecords',empty_records())
    accounts={a['id'] for a in state['investmentAccounts']}
    if any(row['account_id'] not in mapping or mapping[row['account_id']] not in accounts for row in rows):
        raise ValueError('Map every CSV account to an existing investment account.')
    indices={kind:{(row['accountId'],row['date'] if kind=='valuations' else row['id']):i for i,row in enumerate(records[kind])} for kind in ('valuations','flows')}
    added,changed,skipped=[],[],[]
    seen=set()
    for source in rows:
        account_id=mapping[source['account_id']]
        kind='flows' if source['_flow'] else 'valuations'
        row=dict(accountId=account_id,date=source['date'],amount=str(number(source['amount_usd'])),source=source['source'])
        if source['_flow']:row.update(id=source['flow_id'],kind=source['kind'])
        key=account_id,row['id'] if source['_flow'] else row['date']
        if (kind,*key) in seen:raise ValueError(f"Line {source['_line']}: repeated record identity after account mapping.")
        seen.add((kind,*key))
        index=indices[kind].get(key)
        if index is None:
            indices[kind][key]=len(records[kind]);records[kind].append(row);added.append((kind,row))
        else:
            prior=records[kind][index]
            same=number(prior['amount'])==number(row['amount']) and all(prior[field]==row[field] for field in ('date','source')) and (not source['_flow'] or prior['kind']==row['kind'])
            if same:skipped.append((kind,row))
            else:records[kind][index]=row;changed.append((kind,prior,row))
    validate_records(records,accounts)
    return dict(state=candidate,base=deepcopy(state),added=added,changed=changed,skipped=skipped)


def commit_import(store,preview,acknowledge_corrections=False):
    if store.state!=preview['base']:raise ValueError('Records changed after preview. Preview again before saving.')
    if preview['changed'] and not acknowledge_corrections:raise ValueError('Review and acknowledge the existing-record corrections first.')
    store.save(preview['state'])
