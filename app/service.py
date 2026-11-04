"""업데이트와 REF 변경 후 통합 수주현황을 다시 만듭니다."""
from datetime import datetime, timezone
from copy import deepcopy
from .engine import *
from .classifications import DEFAULTS

TARGETS=['ERP','HMD','HHI','HSHI']
def timestamp():return datetime.now(timezone.utc).isoformat()
def ref_overrides(book):
    return {text(cell(r,1)):(text(cell(r,2)),text(cell(r,3))) for r in book.get('REF',[])[1:] if text(cell(r,1))}

def initialize(book):
    if 'ERP' not in book or 'REF' not in book:raise ValueError('최초 등록 파일에는 ERP와 REF 시트가 필요합니다.')
    book=deepcopy(book)
    if 'SET' not in book:book['SET']=[['현장명','주문사양','POR NO.','품목코드','출고잔량','SET']]
    existing={text(cell(r,1)) for r in book['REF'][1:]}
    for k,(name,category) in DEFAULTS.items():
        if k not in existing:book['REF'].append([k,name,category])
    stamp=timestamp()
    return rebuild({'book':book,'updates':{k:stamp for k in TARGETS if k in book},'history':[{'at':stamp,'target':'최초 ZIP 등록'}]})

def rebuild(state):
    erp=convert_erp(state['book'])
    unified=[];warnings=[]
    for name in [*ADAPTERS,'중소']:
        if name!='중소' and name not in state['book']:continue
        rows,notes=build_source(state['book'],erp,name)
        warnings.extend([f'{name}: {v}' for v in notes])
        unified.extend([{'yard':name,'values':r} for r in rows])
    unknown=sorted({r[0] for r in erp if r[1]=='미분류'})
    if unknown:warnings.append('REF 미등록 거래처: '+', '.join(unknown))
    return {**state,'erp':erp,'orders':unified,'warnings':warnings,'unclassified':unknown}

def norm(v):return ''.join(c for c in text(v) if c.isalnum()).casefold()
def replace_raw(state,target,rows):
    if target not in TARGETS:raise ValueError('업데이트할 대상을 선택하세요.')
    updated={**state,'book':deepcopy(state['book']),'updates':dict(state['updates']),'history':state.get('history',[])[:]}
    if target=='ERP':
        header=next((i for i,r in enumerate(rows[:20]) if norm(cell(r,3))=='품목코드' and norm(cell(r,10))=='현장명'),None)
        if header is None:raise ValueError('ERP 원본 머리글이 다릅니다. 거래처부터 시작하는 수주 원본 전체를 업로드하세요.')
        data=rows[header:]
        updated['book']['ERP']=[[None]*19+list(r) for r in data]
    else:
        if target not in updated['book']:raise ValueError(f'최초 ZIP에 {target} 시트가 없습니다. 해당 시트가 있는 기준 파일로 초기 등록하세요.')
        adapter=ADAPTERS[target];template=updated['book'][target]
        expected=template[adapter.header-1][13:]
        # 빨간색 선 오른쪽 RAW는 N열부터 시작합니다.
        # 동일 대상의 전체 시트를 올린 경우도 머리글로 구분합니다.
        found=None
        for i,row in enumerate(rows[:20]):
            for offset in (0,13):
                candidate=row[offset:]
                if len(candidate)<3:continue
                checks=[j for j in range(min(len(expected),len(candidate))) if norm(expected[j])]
                if len(checks)>=3 and all(norm(candidate[j])==norm(expected[j]) for j in checks) and len(candidate)>=max(max(adapter.qty,adapter.due,adapter.destination)-13,49 if target=='HMD' else 0):
                    found=(i,offset);break
            if found:break
        if found is None:raise ValueError(f'{target} RAW 머리글 또는 열 순서가 기준 엑셀과 다릅니다. 빨간색 선 오른쪽 전체 열을 머리글과 함께 업로드하세요.')
        i,offset=found
        records=[[None]*13+list(r[offset:]) for r in rows[i+1:] if any(text(v) for v in r[offset:])]
        # 머리글만 있는 자료는 해당 대상의 전체 자료를 비웁니다.
        updated['book'][target]=deepcopy(template[:adapter.header])+records
    stamp=timestamp();updated['updates'][target]=stamp
    updated['history']=(updated['history']+[{'at':stamp,'target':target+' 전체 교체'}])[-100:]
    return rebuild(updated)

def filter_orders(state,yard='',q='',por='',hull='',code='',block='',destination='',date_from='',date_to=''):
    for order in state['orders']:
        r=order['values']
        if yard and order['yard']!=yard:continue
        if q and q.casefold() not in ' '.join(text(v) for v in [order['yard'],*r]).casefold():continue
        if any(value and value.casefold() not in text(r[index]).casefold() for value,index in [(por,0),(hull,1),(block,2),(code,5),(destination,10)]):continue
        due=text(r[11])
        if date_from and (not due or due<date_from):continue
        if date_to and (not due or due>date_to):continue
        yield order
