"""Immutable, scoped datasets and a finite set of reproducible calculations."""
from collections import defaultdict
from contextlib import closing
import csv
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import io
import json
import re
import sqlite3
from .store import now
from .research import permitted


def number(value):
    if isinstance(value,bool):
        raise ValueError('Boolean is not a numeric measurement')
    try:
        result=Decimal(str(value))
    except (InvalidOperation,ValueError):
        raise ValueError('Expected a finite decimal number') from None
    if not result.is_finite() or abs(result)>Decimal('1e18'):
        raise ValueError('Numeric measurement out of range')
    return result


def money(value):
    return str(value.quantize(Decimal('.01')))


def zone(name):
    if name in {'Asia/Hong_Kong','Asia/Shanghai'}:
        return timezone(timedelta(hours=8))
    if name in {'UTC','Etc/UTC'}:
        return timezone.utc
    from zoneinfo import ZoneInfo
    return ZoneInfo(name)


def register(store,state,task,path,name,provenance,timezone='UTC',currency=None,as_of=None):
    from .tools import safe_path
    from .memory import protect_text
    protect_text(name,100)
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',name):
        raise ValueError('Dataset name must be a short identifier')
    if not isinstance(provenance,dict) or set(provenance)-{'source_ids','description','input_kind'}:
        raise ValueError('Dataset provenance needs source_ids/description/input_kind')
    if provenance.get('input_kind') not in {'observed','operator_snapshot','simulation','user_provided'}:
        raise ValueError('Declare dataset input_kind; simulations are not observations')
    for sid in provenance.get('source_ids',[]):
        if sid not in state['sources'] or not permitted(state['sources'][sid],task):
            raise ValueError('Dataset source outside scope')
    if provenance['input_kind']=='observed' and not any(state['sources'][sid]['kind'] in {'fetched_text','document_text'} for sid in provenance.get('source_ids',[])):
        raise ValueError('Observed research data needs an opened body source; otherwise label it simulation/user_provided')
    zone(timezone)
    if currency is not None and currency not in {'USD','HKD','CNY','EUR','GBP','mixed'}:
        raise ValueError('Unsupported currency declaration')
    root=store.workspace(task['owner'])
    rooms=[]
    if path.endswith('.sqlite3'):
        if not re.fullmatch(r'inputs/bookings-[a-f0-9]{16}\.sqlite3',path):
            raise ValueError('Only operator-provided read-only booking snapshots may be registered')
        candidate=root/path
        if candidate.is_symlink() or not candidate.resolve().is_relative_to(root.resolve()) or not candidate.is_file():
            raise ValueError('Snapshot outside workspace')
        manifest=json.loads(candidate.with_suffix('.json').read_text(encoding='utf-8'))
        raw=candidate.read_bytes()
        if len(raw)>20*1024*1024 or hashlib.sha256(raw).hexdigest()!=manifest['sha256']:
            raise ValueError('Snapshot provenance/hash mismatch')
        archived=store.path/'data-snapshots'/manifest['sha256']
        # Snapshot manager archives .sqlite3; a workspace-written fake manifest
        # alone cannot grant permission to register arbitrary binary databases.
        archived=archived.with_suffix('.sqlite3')
        if not archived.is_file() or hashlib.sha256(archived.read_bytes()).hexdigest()!=manifest['sha256']:
            raise ValueError('Snapshot not authorized by the operator')
        with closing(sqlite3.connect(candidate.resolve().as_uri()+'?mode=ro',uri=True)) as db:
            db.execute('PRAGMA trusted_schema=OFF')
            db.row_factory=sqlite3.Row
            rows=[dict(r) for r in db.execute('SELECT id,room_id,start_dt,end_dt,status FROM bookings LIMIT 10001')]
            rooms=[dict(r) for r in db.execute('SELECT id,name FROM rooms LIMIT 101')]
    else:
        candidate=safe_path(root,path,True)
        raw=candidate.read_bytes()
        if len(raw)>2*1024*1024:
            raise ValueError('Dataset exceeds 2 MB')
        if candidate.suffix=='.csv':
            rows=list(csv.DictReader(io.StringIO(raw.decode('utf-8-sig'))))
        elif candidate.suffix=='.json':
            parsed=json.loads(raw)
            if isinstance(parsed,dict):
                if set(parsed)-{'records','bookings','rooms'}:
                    raise ValueError('JSON dataset must be records, or bookings/rooms')
                rows=parsed.get('records',parsed.get('bookings',[]))
                rooms=parsed.get('rooms',[])
            else:
                rows=parsed
        else:
            raise ValueError('Register CSV/JSON or an operator booking snapshot')
    if not isinstance(rows,list) or len(rows)>10000 or not isinstance(rooms,list) or len(rooms)>100:
        raise ValueError('Dataset row/room limits exceeded')
    clean=[]
    clean_rooms=[]
    for room in rooms:
        if not isinstance(room,dict) or not {'id','name'}<=set(room) or not isinstance(room['name'],str) or len(room['name'])>200 or type(room['id']) not in {int,str} or len(str(room['id']))>64:
            raise ValueError('Rooms metadata requires bounded id/name entries')
        clean_rooms.append({'id':room['id'],'name':room['name']})
    rooms=clean_rooms
    for row in rows:
        if not isinstance(row,dict) or len(row)>50 or any(not isinstance(k,str) for k in row):
            raise ValueError('Dataset rows must be bounded objects')
        record={k:v for k,v in row.items() if k.lower() not in {'booker','title','email','phone','password','api_key'}}
        if any(isinstance(v,(dict,list)) or (v is not None and len(str(v))>2048) for v in record.values()):
            raise ValueError('Dataset cells must be scalar and bounded')
        clean.append(record)
    protect_text(json.dumps({'records':clean,'rooms':rooms,'provenance':provenance},ensure_ascii=False),5*1024*1024)
    payload={'records':clean,'rooms':rooms,'timezone':timezone,'currency':currency,'as_of':as_of}
    encoded=json.dumps(payload,ensure_ascii=False,sort_keys=True,allow_nan=False).encode()
    digest=hashlib.sha256(encoded).hexdigest()
    datasets=state.setdefault('datasets',{})
    scope_key=(task['id'] if task.get('private') else 'public')+':'+name
    old_id=state.setdefault('dataset_heads',{}).get(scope_key)
    if old_id and datasets[old_id]['sha256']==digest:
        return datasets[old_id]
    folder=store.path/'datasets'
    folder.mkdir(exist_ok=True)
    (folder/digest).write_bytes(encoded)
    item={'id':'ds'+str(len(datasets)+1),'name':name,'key':scope_key,'sha256':digest,
          'source_sha256':hashlib.sha256(raw).hexdigest(),'rows':len(clean),
          'schema':sorted({k for row in clean for k in row}),'timezone':timezone,
          'currency':currency,'as_of':as_of,'provenance':provenance,'created_at':now(),
          'owner':task['owner'],'task':task['id'],'private':bool(task.get('private')),
          'version':datasets[old_id]['version']+1 if old_id else 1,'supersedes':old_id,
          'redacted_fields':['booker','title','email','phone','password','api_key']}
    datasets[item['id']]=item
    state['dataset_heads'][scope_key]=item['id']
    return item


def load(store,state,task,dataset_id):
    item=state.get('datasets',{}).get(dataset_id)
    if not item or not permitted(item,task):
        raise ValueError('Dataset unavailable in this scope')
    raw=(store.path/'datasets'/item['sha256']).read_bytes()
    if hashlib.sha256(raw).hexdigest()!=item['sha256']:
        raise ValueError('Dataset integrity failure')
    return item,json.loads(raw)


def booking_summary(payload,spec):
    if set(spec)-{'op','start_date','end_date'} or not {'start_date','end_date'}<=set(spec):
        raise ValueError('Booking summary needs inclusive start_date/end_date')
    tz=zone(payload['timezone'])
    lo=datetime.strptime(spec['start_date'],'%Y-%m-%d').replace(tzinfo=tz)
    hi=datetime.strptime(spec['end_date'],'%Y-%m-%d').replace(tzinfo=tz)+timedelta(days=1)
    if not 0<(hi-lo).days<=366:
        raise ValueError('Booking window must be 1-366 days')
    def parse(text):
        value=datetime.fromisoformat(text)
        return value.replace(tzinfo=tz) if value.tzinfo is None else value.astimezone(tz)
    rooms={str(r['id']):{'room_id':r['id'],'name':r['name'],'bookings':0,'active':0,'cancelled':0,'seconds':Decimal(0)} for r in payload.get('rooms',[])}
    days=defaultdict(Decimal)
    hours=defaultdict(Decimal)
    steps=0
    for row in payload['records']:
        start,end=parse(row['start_dt']),parse(row['end_dt'])
        if end<=start or row['status'] not in {'active','cancelled'}:
            raise ValueError('Invalid booking interval/status')
        start,end=max(lo,start),min(hi,end)
        if end<=start:
            continue
        key=str(row['room_id'])
        room=rooms.setdefault(key,{'room_id':row['room_id'],'name':'Room #'+key,'bookings':0,'active':0,'cancelled':0,'seconds':Decimal(0)})
        room['bookings']+=1
        room[row['status']]+=1
        if row['status']=='cancelled':
            continue
        seconds=Decimal(str((end-start).total_seconds()))
        room['seconds']+=seconds
        cursor=start
        while cursor<end:
            edge=min(end,cursor.replace(minute=0,second=0,microsecond=0)+timedelta(hours=1))
            amount=Decimal(str((edge-cursor).total_seconds()))
            days[cursor.date().isoformat()]+=amount
            hours[f'{cursor.hour:02d}:00']+=amount
            cursor=edge
            steps+=1
            if steps>100000:
                raise ValueError('Analysis interval expansion exceeds bounded work limit')
    result=[]
    for room in rooms.values():
        result.append({k:v for k,v in room.items() if k!='seconds'} | {
            'reserved_hours':str(room['seconds']/Decimal(3600)),
            'cancellation_rate':str(Decimal(room['cancelled'])/room['bookings']) if room['bookings'] else None})
    return {'rooms':result,'busy_days':{k:str(v/3600) for k,v in sorted(days.items())},
            'busy_hours':{k:str(v/3600) for k,v in sorted(hours.items())},
            'formula':'Count scheduled intervals overlapping [start 00:00, day-after-end 00:00). Active reserved hours = sum(clipped seconds)/3600. Cancelled/ALL overlapping records. Cancelled durations excluded; hour bins use declared timezone.',
            'timezone':payload['timezone'],'window':[lo.isoformat(),hi.isoformat()]}


def allocation(payload,spec):
    if set(spec)-{'op','capital_hkd','hkd_per_usd','fx_as_of','fee_rate','shocks'}:
        raise ValueError('Unknown allocation parameter')
    capital=number(spec['capital_hkd'])
    fx=number(spec['hkd_per_usd'])
    fee=number(spec.get('fee_rate','0'))
    if capital<=0 or fx<=0 or not 0<=fee<1 or not spec.get('fx_as_of'):
        raise ValueError('Positive capital/FX, dated FX and valid fee rate required')
    weights=[number(r['weight']) for r in payload['records']]
    if any(not 0<=w<=1 for w in weights) or sum(weights)!=Decimal(1):
        raise ValueError('Portfolio weights must sum exactly to 1, including explicit cash')
    rows=[]
    shock_total=Decimal(0)
    assets={r['asset'] for r in payload['records']}
    if len(assets)!=len(payload['records']) or set(spec.get('shocks',{}))-assets:
        raise ValueError('Unique assets and known scenario shock keys required')
    for row,weight in zip(payload['records'],weights):
        asset=row['asset']
        cash=asset.upper()=='CASH'
        if row.get('currency','HKD' if cash else 'USD')!=('HKD' if cash else 'USD'):
            raise ValueError('Allocation convention requires USD investments and HKD cash')
        shock=number(spec.get('shocks',{}).get(asset,'0'))
        if not -1<=shock<=10:
            raise ValueError('Scenario return out of range')
        gross=capital*weight
        fees=Decimal(0) if cash else gross*fee
        net=gross-fees
        rows.append({'asset':asset,'weight':str(weight),'gross_hkd':money(gross),
                     'fee_hkd':money(fees),'net_hkd':money(net),
                     'net_usd':money(net/fx) if not cash else None,'scenario_return':str(shock)})
        shock_total+=net*shock
    return {'positions':rows,'capital_hkd':money(capital),'weights_sum':str(sum(weights)),
            'fees_hkd':money(sum((Decimal(r['fee_hkd']) for r in rows),Decimal(0))),
            'scenario_pnl_hkd':money(shock_total),'scenario_label':'Explicit hypothetical shocks, not forecast or calibrated risk',
            'formula':'gross_hkd=capital*weight; fees=gross*fee_rate (cash exempt); net_usd=(gross-fees)/hkd_per_usd; scenario P&L=sum(net_hkd*explicit shock). Fractional simulated amounts; no trade execution.',
            'hkd_per_usd':str(fx),'fx_as_of':spec['fx_as_of']}


def analyze(store,state,task,dataset_id,spec):
    item,payload=load(store,state,task,dataset_id)
    if not isinstance(spec,dict) or not isinstance(spec.get('op'),str):
        raise ValueError('Finite analysis spec required')
    if spec['op']=='booking_summary':
        result=booking_summary(payload,spec)
    elif spec['op']=='allocation':
        result=allocation(payload,spec)
    elif spec['op']=='group_sum':
        if set(spec)!={'op','group_by','column'}:
            raise ValueError('group_sum needs group_by and column')
        sums=defaultdict(Decimal)
        for row in payload['records']:
            sums[str(row[spec['group_by']])]+=number(row[spec['column']])
        result={'groups':{k:str(v) for k,v in sorted(sums.items())},'formula':'Exact Decimal sum grouped by explicit column; no eval or SQL.'}
    elif spec['op']=='detector_base_rate':
        if set(spec)!={'op'} or len(payload['records'])!=1:
            raise ValueError('Use one declared detector scenario record')
        r=payload['records'][0]
        prevalence,sensitivity,specificity=[number(r[k]) for k in ('prevalence','sensitivity','specificity')]
        if not all(0<=v<=1 for v in (prevalence,sensitivity,specificity)):
            raise ValueError('Probabilities must be 0-1')
        tp=prevalence*sensitivity
        fp=(1-prevalence)*(1-specificity)
        result={'true_positive_share':str(tp),'false_positive_share':str(fp),
                'positive_predictive_value':str(tp/(tp+fp)) if tp+fp else None,
                'formula':'PPV = prevalence*sensitivity / (prevalence*sensitivity + (1-prevalence)*(1-specificity))',
                'limitation':'Only the stated hypothetical population/operating point; a score cannot establish authorship.'}
    else:
        raise ValueError('Unsupported analysis; custom code must run in the offline sandbox with an exported snapshot')
    return {'dataset_id':dataset_id,'dataset_sha256':item['sha256'],'dataset_version':item['version'],
            'input_kind':item['provenance']['input_kind'],'spec':spec,'result':result,'computed_at':now()}


def export_dataset(store,state,task,dataset_id,path):
    from .tools import safe_path
    item,payload=load(store,state,task,dataset_id)
    target=safe_path(store.workspace(task['owner']),path)
    if target.suffix!='.json':
        raise ValueError('Dataset export currently requires JSON')
    encoded=json.dumps(payload,ensure_ascii=False,sort_keys=True).encode()
    if target.exists() and target.read_bytes()!=encoded:
        raise ValueError('Refusing to overwrite a different dataset export')
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_bytes(encoded)
    return {'dataset_id':dataset_id,'path':path,'sha256':hashlib.sha256(encoded).hexdigest(),'redacted':True}
