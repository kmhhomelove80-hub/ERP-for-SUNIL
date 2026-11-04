"""조선소 어댑터와 공통 변환/추출. 모든 열 번호는 엑셀의 1부터 시작."""
from dataclasses import dataclass
from datetime import datetime, date, timedelta
from decimal import Decimal, InvalidOperation
import re

HEADERS = ['POR번호','호선','블록','품명','수량','ERP CODE','합계','SET','수주일자','수주번호','납품처','납기요구일']
ERP_HEADERS = ['조선소','구분','호선','주문사양','SER/SEQ','현장명','주문사양','SER/SEQ','POR','품목코드','수주일자','수주번호','출고잔량','공급가액','부가세','합계금액','납기일정']

def text(v):
    if v is None: return ''
    if isinstance(v, float) and v.is_integer(): return str(int(v))
    return str(v).strip()

def cell(row, n): return row[n-1] if len(row) >= n else None

def number(v):
    if text(v) == '': return None
    try: x = Decimal(text(v).replace(',', ''))
    except InvalidOperation: raise ValueError(f'숫자로 읽을 수 없는 수량/금액: {text(v)}')
    if not x.is_finite(): raise ValueError('수량/금액은 유한한 숫자여야 합니다.')
    return int(x) if x == int(x) else float(x)

def day(v):
    if text(v) == '': return None
    if isinstance(v, datetime): return v.date().isoformat()
    if isinstance(v, date): return v.isoformat()
    if isinstance(v, (int,float)): return (datetime(1899,12,30)+timedelta(days=v)).date().isoformat()
    for fmt in ('%Y-%m-%d','%Y/%m/%d','%m/%d/%Y','%y.%m.%d','%Y.%m.%d'):
        try: return datetime.strptime(text(v),fmt).date().isoformat()
        except ValueError: pass
    try:return datetime.fromisoformat(text(v)).date().isoformat()
    except ValueError:raise ValueError(f'날짜 형식을 확인하세요: {text(v)}')

@dataclass(frozen=True)
class Adapter:
    name: str
    header: int
    last_col: int
    hull: int
    block: int
    title: int
    qty: int
    destination: int
    due: int
    por_cols: tuple

# SHI·ICOPS는 규칙이 확정되면 이 등록부와 전용 보정 함수로 추가합니다.
ADAPTERS = {
    'HHI': Adapter('HHI',3,16,19,20,24,26,32,30,(16,)),
    'HMD': Adapter('HMD',3,14,14,18,20,58,61,25,(16,17)),
    'HSHI': Adapter('HSHI',1,14,14,15,20,41,36,29,(17,18)),
}

def convert_erp(book, external=None, overrides=None):
    refs = book.get('REF', [])
    if len(refs)<2: raise ValueError('기준 엑셀에 REF 시트가 필요합니다.')
    refmap = {}
    for r in refs[1:]:
        if text(cell(r,1)): refmap.setdefault(text(cell(r,1)), (text(cell(r,2)), text(cell(r,3))))
    refmap.update(overrides or {})
    if external is not None:
        rows = external
    else:
        rows = [r[19:55] for r in book.get('ERP',[])]
    if not rows or text(cell(rows[0],3)) != '품목코드' or text(cell(rows[0],10)) != '현장명':
        raise ValueError('ERP 원본 열 구성이 다릅니다. 거래처부터 시작하는 원본 파일 또는 ERP T열 이후 자료가 필요합니다.')
    out=[]; missing=[]
    for line,r in enumerate(rows[1:],2):
        if not text(cell(r,3)): continue
        customer_key = text(cell(r,1))
        if customer_key not in refmap:
            missing.append(f'{line}행 {customer_key}')
        customer, category = refmap.get(customer_key, (customer_key, '미분류'))
        raw_hull=text(cell(r,10)); raw_order=text(cell(r,8)).replace('-',''); seq=text(cell(r,11)).replace('-','')
        hull=raw_hull or (raw_order if category=='중소' else '')
        if category=='대형' and hull[:1].upper() in ('H','M'): hull=hull[1:].strip()
        hull_len=len(hull)
        if category=='대형' and hull_len in (10,8): hull=hull[:4]
        else:
            if hull[:2].upper()=='SN': hull=hull[2:]
            for part in (' (부가세)','(부가세)','스페어파트','-'): hull=hull.replace(part,'')
            hull=hull.strip()
            if hull.endswith('06'): hull=hull[:-2]
        if hull_len==10 and len(hull)>4: hull=hull[:4]
        order=raw_order
        if not order:
            if '삼성중공업' in customer:
                if len(seq)==17: order=seq
                elif len(seq)==11: order='SN'+hull+seq
            elif '현대삼호' in customer: order=hull+seq
            elif '현대중공업' in customer: order=seq if 'M' not in customer and len(seq)==10 else hull+seq
        elif category=='대형' and len(raw_order)==9:
            if '현대삼호' in customer: order=order[4:]
            elif '현대중공업' in customer or '현대미포' in customer: order=order[3:]
        por=(raw_hull or raw_order) if category=='중소' else order
        if category=='대형' and '현대삼호' in customer and hull and order and not order.startswith(hull): por=hull+order
        out.append([customer,category,hull,order,seq,len(hull),len(order),len(seq),por,text(cell(r,3)),day(cell(r,12)),number(cell(r,20)),number(cell(r,22)),number(cell(r,28)),number(cell(r,29)),number(cell(r,30)),day(cell(r,16)),text(cell(r,4))])
    return out

def complex_key(d,n,c,q): return (day(d),text(number(n)) if text(n) else '',text(c),number(q))

def build_source(book, erp, name):
    if name=='중소':
        return [[r[8],r[2],r[3],r[17],r[12],r[9],None,None,r[10],r[11],None,r[16]] for r in erp if r[1]=='중소'], []
    if name not in ADAPTERS: raise ValueError('이 조선소는 아직 처리 규칙이 준비되지 않았습니다.')
    a=ADAPTERS[name]; rows=book.get(name,[])
    if len(rows)<a.header: raise ValueError(f'{name} 시트의 원본 머리글이 없습니다.')
    if len(rows)==a.header:return [],[]
    emap={}; smap={}; warnings=[]
    for r in erp:
        if r[8]: emap.setdefault(text(r[8]),r)
    for r in book.get('SET',[])[1:]:
        if text(cell(r,3)): smap.setdefault(text(cell(r,3)),cell(r,6))
    out=[]; missing=0
    for r in rows[a.header:]:
        if not text(cell(r,a.last_col)): continue
        por=''.join(text(cell(r,c)) for c in a.por_cols)
        hull=text(cell(r,a.hull)); destination=text(cell(r,a.destination)); due=cell(r,a.due)
        if name=='HMD':
            hull=hull[:4]
            if text(cell(r,42)):
                due=cell(r,42)
                destination=text(cell(r,45)) or text(cell(r,62))
        e=emap.get(por)
        if not e: missing+=1
        out.append([por,hull,text(cell(r,a.block)),text(cell(r,a.title)),number(cell(r,a.qty)),e[9] if e else None,None,smap.get(por),e[10] if e else None,e[11] if e else None,destination,day(due)])
    if missing: warnings.append(f'원본 중 ERP POR 조회 실패 {missing}행: 품목코드·수주정보가 비어 있을 수 있습니다.')
    return out,warnings

def extract(book, erp, name, criteria):
    source,warnings=build_source(book,erp,name)
    unknown=sorted({r[0] for r in erp if r[1]=='미분류'})
    if unknown:warnings.append('REF 미등록 거래처는 중소 추출에서 제외됩니다: '+', '.join(unknown))
    # POR 빈 목록은 전체 추출. 조건 목록의 각 행은 OR, POR와 블록은 AND.
    def matches(r):
        if not criteria:return True
        return any((not c['por'] or text(r[0])==c['por']) and (name!='HSHI' or not c['block'] or text(r[2])==c['block']) for c in criteria)
    selected=[r[:] for r in source if matches(r)]
    before=len(selected)
    if name=='HMD':selected=[r for r in selected if text(r[10])]
    removed=before-len(selected)
    simple={}; complex_map={}
    for r in erp:
        if text(r[8]):simple.setdefault(text(r[8]),r[13:16])
        complex_map.setdefault(complex_key(r[10],r[11],r[9],r[12]),r[13:16])
    data=[]; missing_money=0
    for r in selected:
        money=complex_map.get(complex_key(r[8],r[9],r[5],r[4])) if name=='중소' else simple.get(text(r[0]))
        if money is None:missing_money+=1
        data.append({'values':r+(money if money is not None else [None]*3),'unmatched':money is None})
    groups={}; conflicts=0
    for record in data:
        r=record['values']; key=tuple(text(r[i]) for i in (0,1,2,3,5))
        if key in groups:
            target=groups[key]
            if target['values'][7:]!=r[7:]:conflicts+=1
            target['values'][4]=(target['values'][4] or 0)+(r[4] or 0)
            target['unmatched'] |= record['unmatched']
        else:groups[key]={'values':r[:],'unmatched':record['unmatched']}
    result=sorted(groups.values(),key=lambda r:text(r['values'][5]).casefold())
    sums={}
    for rec in result:
        r=rec['values'];k=text(r[5]).casefold();sums[k]=sums.get(k,0)+(r[4] or 0)
    for rec in result:rec['values'][6]=sums[text(rec['values'][5]).casefold()]
    if conflicts:warnings.append(f'통합된 {conflicts}행의 부가정보가 달라 첫 번째 행의 납기·납품처·금액을 유지했습니다.')
    if missing_money:warnings.append(f'ERP 금액 조회 실패 {missing_money}행: 결과에 표시했습니다.')
    return {'rows':result,'headers':HEADERS+['공급가액','부가세','합계금액'],'warnings':warnings,'stats':{'selected':before,'removed':removed,'consolidated':len(data)-len(result),'final':len(result),'quantity':sum(r['values'][4] or 0 for r in result)},'source_rows':source}
