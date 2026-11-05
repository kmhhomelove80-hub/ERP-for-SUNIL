"""열 순서와 무관한 RAW 헤더 매칭·수동 연결 미리보기."""
import re,unicodedata
from .engine import cell,text,ADAPTERS

PURPOSES={
 'ERP':{1:'거래처',3:'품목코드',4:'품명',8:'주문사양',10:'현장명',11:'SER/SEQ',12:'수주일자',16:'납기일정',20:'수주번호',22:'출고잔량',28:'공급가액',29:'부가세',30:'합계금액'},
 'HMD':{14:'호선',16:'POR 앞부분',17:'POR 행번',18:'블록',20:'품명',25:'기본 납기',42:'배송요구일',45:'수령자',58:'수량',61:'기본 납품처',62:'대체 입고장소'},
 'HHI':{16:'POR',19:'호선',20:'블록',24:'품명',26:'수량',30:'납기',32:'납품처'},
 'HSHI':{14:'호선',15:'블록',17:'POR 앞부분',18:'POR 행번',20:'품명',29:'납기',36:'납품처',41:'수량'}}

def header_key(v):return re.sub(r'\s+','',unicodedata.normalize('NFKC',text(v))).casefold()
def specification(state,target):
 if target=='ERP':
  return state['book']['ERP'][0][19:],PURPOSES['ERP'],0
 if target in ADAPTERS:
  a=ADAPTERS[target];return state['book'][target][a.header-1][13:],{k-13:v for k,v in PURPOSES[target].items()},13
 cfg=state.get('yards',{}).get(target)
 if not cfg:raise ValueError('업데이트 대상을 확인하세요.')
 required={c:'ERP 매칭' for c in cfg['match_cols']}
 for key,label in [('hull','호선'),('block','블록'),('title','품명'),('qty','수량'),('destination','납품처'),('due','납기')]:
  if cfg.get(key):required[cfg[key]]=label
 return state['book'][target][cfg['header_row']-1],required,0

def plan_raw(state,target,rows,header_row=0):
 expected,required,offset=specification(state,target)
 if not rows:raise ValueError('원본이 비어 있습니다.')
 aliases=state.get('raw_aliases',{}).get(target,{})
 names=[header_key(aliases.get(str(c),cell(expected,c))) for c in required]
 if header_row:
  if not 1<=header_row<=min(20,len(rows)):raise ValueError('머리글 행 번호를 확인하세요.')
  index=header_row-1
 else:
  index=max(range(min(20,len(rows))),key=lambda i:sum(1 for v in rows[i] if header_key(v) in names and header_key(v)))
 incoming=rows[index]
 # 전체 가공 시트를 올린 경우 기존 A~L 결과 열은 RAW 후보에서 제외
 start=13 if target in ADAPTERS and header_key(cell(incoming,1))=='por번호' else 0
 fields=[]
 for c,name in enumerate(expected,1):
  if not text(name) and c not in required:continue
  remembered=aliases.get(str(c),name)
  candidates=[j+1 for j,v in enumerate(incoming) if j>=start and header_key(v)==header_key(remembered) and header_key(v)]
  fields.append({'column':c,'header':text(name),'purpose':required.get(c,''),'required':c in required,'source':candidates[0] if len(candidates)==1 else None,'ambiguous':len(candidates)>1,'remembered':text(remembered)})
 unresolved=[f for f in fields if f['required'] and not f['source']]
 return {'target':target,'header_row':index+1,'incoming':[text(v) for v in incoming],'fields':fields,'unresolved':len(unresolved),'data_count':sum(any(text(v) for v in r) for r in rows[index+1:]),'preview':rows[index+1:index+6]}

def materialize(state,target,rows,plan,mapping):
 expected,required,_=specification(state,target)
 selected={f['column']:f['source'] for f in plan['fields']}
 for k,v in mapping.items():
  c=int(k)
  if not 1<=c<=len(expected):raise ValueError('연결할 대상 열 범위가 올바르지 않습니다.')
  selected[c]=int(v) if v else None
 for c in required:
  if not selected.get(c):raise ValueError(f'{required[c]}에 연결할 원본 열을 선택하세요.')
 if any(v is not None and not 1<=v<=len(plan['incoming']) for v in selected.values()):raise ValueError('원본 열 범위를 확인하세요.')
 canonical=[list(expected)]
 for raw in rows[plan['header_row']:]:
  if any(text(v) for v in raw):canonical.append([cell(raw,selected[c]) if selected.get(c) else None for c in range(1,len(expected)+1)])
 aliases={str(c):plan['incoming'][v-1] for c,v in selected.items() if v and text(plan['incoming'][v-1])}
 return canonical,aliases
