from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import HTMLResponse, Response
from pathlib import Path
from pydantic import BaseModel, Field
from typing import Literal
import zipfile
from copy import deepcopy
import io, logging, os
from .store import Store
from .files import read_book, read_raw
from .service import initialize, replace_raw, rebuild, filter_orders, timestamp, TARGETS
from .engine import extract, extract_selected, HEADERS, ADAPTERS, text, product_key
from .exporting import export_result, styled_sheet
from openpyxl import Workbook

ROOT=Path(__file__).parent
STORE=Store(Path(os.environ.get('DELIVERY_DATA_DIR',str(ROOT.parent/'data')))/'workspace.sqlite3')
app=FastAPI(title='선일 수주관리',version='4.0')
from fastapi.staticfiles import StaticFiles
app.mount('/static',StaticFiles(directory=ROOT/'static'),name='static')

@app.exception_handler(ValueError)
async def value_error(request,exc):
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=400,content={'detail':str(exc)})

@app.exception_handler(zipfile.BadZipFile)
async def bad_zip(request,exc):
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=400,content={'detail':'압축 또는 엑셀 파일을 읽지 못했습니다. 파일이 손상되지 않았는지 확인하세요.'})

@app.exception_handler(PermissionError)
async def permission_error(request,exc):
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=400,content={'detail':'파일 접근 권한이 없습니다. 백업 경로의 쓰기 권한 또는 열려 있는 파일을 확인하세요. 자료는 유지됩니다.'})

@app.exception_handler(OSError)
async def file_error(request,exc):
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=400,content={'detail':'파일 저장 또는 읽기에 실패했습니다. 디스크 여유 공간·백업 경로를 확인하세요. 마감 대상은 저장 성공 전에는 목록에서 제거하지 않습니다.'})

@app.get('/',response_class=HTMLResponse)
def home():return (ROOT/'static/index.html').read_text()
@app.get('/favicon.ico',include_in_schema=False)
def favicon():return Response(status_code=204)

def current():
    state=STORE.read()
    if state is None:raise ValueError('업데이트 탭에서 최초 기준 ZIP을 등록하세요.')
    if state.get('schema_version',0)<4:
        state=STORE.commit(rebuild({**state,'schema_version':4}),state['revision'])
    return state

def metadata(state):
    if state is None:return {'initialized':False,'revision':0,'yards':[],'counts':{},'updates':{},'history':[],'warnings':[],'unclassified':[]}
    counts={n:sum(r['yard']==n for r in state['orders']) for n in [*ADAPTERS,*state.get('yards',{}),'중소']}
    return {'initialized':True,'revision':state['revision'],'yards':list(counts),'counts':counts,'erp_count':len(state['erp']),'updates':state['updates'],'history':state.get('history',[])[-12:][::-1],'warnings':state['warnings'],'unclassified':state['unclassified'],'update_targets':['ERP',*ADAPTERS,*state.get('yards',{})],'yard_configs':state.get('yards',{})}

@app.get('/api/state')
def state():return metadata(current() if STORE.read() else None)

async def bytes_of(file):
    data=await file.read(25*1024*1024+1)
    if len(data)>25*1024*1024:raise ValueError('파일은 25MB 이하로 업로드하세요.')
    if not data:raise ValueError('파일이 비어 있습니다.')
    return data

@app.post('/api/initialize')
async def init(file:UploadFile=File(...),revision:int=Form(0),confirm:bool=Form(False)):
    if STORE.read() and not confirm:raise ValueError('전체 초기화는 기존 원본과 REF를 교체합니다. 확인 후 실행하세요.')
    try:
        book=read_book(await bytes_of(file),file.filename or '')
        # 이전 버전에서 사용자가 등록한 거래처 분류도 최초 가져오기에 반영
        from .classifications import ClassificationStore
        saved=ClassificationStore().all()
        refs=book.get('REF',[])
        positions={text(r[0]):i for i,r in enumerate(refs) if r and i>0}
        for customer,(name,category) in saved.items():
            row=[customer,name,category]
            if customer in positions:refs[positions[customer]][:3]=row
            elif refs:refs.append(row)
        result=STORE.commit(initialize(book),revision)
        return metadata(result)
    except (ValueError,HTTPException):raise
    except Exception as e:
        logging.exception('최초 자료 읽기 실패')
        raise ValueError(f'엑셀을 읽지 못했습니다 ({type(e).__name__}). 파일 형식을 확인하세요.')

@app.post('/api/update/{target}')
async def update(target:str,file:UploadFile=File(...),revision:int=Form(...)):
    old=current()
    rows=read_raw(await bytes_of(file),file.filename or '',target)
    result=replace_raw(old,target,rows)
    return metadata(STORE.commit(result,revision))

@app.get('/api/orders')
def orders(yard:str='',q:str='',por:str='',hull:str='',code:str='',block:str='',destination:str='',date_from:str='',date_to:str='',page:int=1,page_size:int=100):
    if page<1 or page_size not in (50,100,200):raise ValueError('페이지 범위가 올바르지 않습니다.')
    state=current();rows=list(filter_orders(state,yard,q,por,hull,code,block,destination,date_from,date_to))
    start=(page-1)*page_size
    return {'headers':['조선소',*HEADERS],'total':len(rows),'quantity':sum(r['values'][4] or 0 for r in rows),'rows':rows[start:start+page_size],'revision':state['revision']}

@app.get('/api/orders/download')
def orders_download(yard:str='',q:str='',por:str='',hull:str='',code:str='',block:str='',destination:str='',date_from:str='',date_to:str=''):
    rows=list(filter_orders(current(),yard,q,por,hull,code,block,destination,date_from,date_to))
    w=Workbook();w.remove(w.active);styled_sheet(w,'수주현황',['조선소',*HEADERS],[[r['yard'],*r['values']] for r in rows]);out=io.BytesIO();w.save(out)
    return excel(out.getvalue(),'orders.xlsx')

class Criterion(BaseModel):
    por:str=Field(default='',max_length=200)
    block:str=Field(default='',max_length=200)
class Extraction(BaseModel):
    yard:str=Field(default='',max_length=30)
    order_ids:list[str]|None=Field(default=None,min_length=1,max_length=50000)
    criteria:list[Criterion]=Field(default_factory=list,max_length=5000)
    revision:int

def extraction(req):
    state=current()
    if req.revision!=state['revision']:raise ValueError('자료가 변경됐습니다. 최신 자료로 다시 추출하세요.')
    if req.order_ids is not None:
        indexed={f"{r['yard']}:{i}":r for i,r in enumerate(state['orders'])}
        if any(id not in indexed for id in req.order_ids):raise ValueError('선택한 행을 찾지 못했습니다. 수주현황을 다시 조회하세요.')
        return extract_selected([indexed[id] for id in dict.fromkeys(req.order_ids)],state['erp']),state
    if req.yard not in [*ADAPTERS,*state.get('yards',{}),'중소']:raise ValueError('추출 대상을 확인하세요.')
    selected=[r for r in state['orders'] if r['yard']==req.yard and (not req.criteria or any((not c.por.strip() or text(r['values'][0])==c.por.strip()) and (req.yard not in ['HSHI',*state.get('yards',{})] or not c.block.strip() or text(r['values'][2])==c.block.strip()) for c in req.criteria))]
    return extract_selected(selected,state['erp']),state

@app.post('/api/extract')
def preview(req:Extraction):
    result,state=extraction(req)
    # 미리보기를 중간에서 자르지 않아 합계 병합 구간이 끊기지 않음
    return {k:v for k,v in result.items() if k!='source_rows'} | {'revision':state['revision']}
@app.post('/api/extract/download')
def download(req:Extraction):
    result,state=extraction(req)
    return excel(export_result(result,state['erp']),'delivery-list.xlsx')
def excel(data,name):return Response(data,media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',headers={'Content-Disposition':f'attachment; filename="{name}"'})

@app.get('/api/ref')
def refs(kind:Literal['customers','sets']='customers',q:str='',page:int=1):
    if page<1:raise ValueError('페이지 범위가 올바르지 않습니다.')
    state=current();source=state['book']['REF' if kind=='customers' else 'SET'];rows=[]
    for i,r in enumerate(source[1:],1):
        values=(r+[None]*6)[:3] if kind=='customers' else [(r+[None]*6)[2],(r+[None]*6)[5]]
        if not text(values[0]):continue
        if q and q.casefold() not in ' '.join(text(v) for v in values).casefold():continue
        rows.append({'id':i,'values':values})
    return {'rows':rows[(page-1)*100:page*100],'total':len(rows),'revision':state['revision'],'unclassified':state['unclassified']}

class RefChange(BaseModel):
    kind:Literal['customers','sets']
    id:int|None=Field(default=None,ge=1)
    values:list[str]=Field(min_length=2,max_length=3)
    revision:int
@app.post('/api/ref')
def save_ref(req:RefChange):
    old=current();book=deepcopy(old['book']);key='REF' if req.kind=='customers' else 'SET'
    if req.kind=='customers':
        if len(req.values)!=3 or req.values[2] not in ('대형','중소'):raise ValueError('거래처명·조선소명·구분을 입력하세요.')
        values=[v.strip() for v in req.values]
        if not all(values):raise ValueError('거래처명·조선소명·구분을 입력하세요.')
        if any(text(r[0])==values[0] for i,r in enumerate(book[key][1:],1) if r and i!=req.id):raise ValueError('같은 거래처명이 이미 있습니다. 기존 항목을 수정하세요.')
    else:
        if len(req.values)!=2 or not all(v.strip() for v in req.values):raise ValueError('POR와 SET 표시를 입력하세요.')
        values=[None,None,req.values[0].strip(),None,None,req.values[1].strip()]
    if req.id is None:book[key].append(values)
    elif req.id>=len(book[key]):raise ValueError('수정할 항목이 없습니다. 새로고침하세요.')
    else:
        if req.kind=='customers':book[key][req.id][:3]=values
        else:
            row=book[key][req.id];row.extend([None]*max(0,6-len(row)));row[2]=values[2];row[5]=values[5]
    stamp=timestamp()
    updated=rebuild({**old,'book':book,'history':(old.get('history',[])+[{'at':stamp,'target':'REF '+('거래처' if req.kind=='customers' else 'SET')+' 저장'}])[-100:]})
    return metadata(STORE.commit(updated,req.revision))

# 출고내역은 확정 시점의 값을 별도 원장에 누적합니다.
from .shipments import Shipments, STATUSES
import hashlib

def shipment_store():return Shipments(STORE.path)

class ShipmentConfirm(Extraction):
    request_key:str=Field(min_length=16,max_length=100)

@app.post('/api/shipments/confirm')
def confirm_shipments(req:ShipmentConfirm):
    signature=hashlib.sha256(req.model_dump_json(exclude={'request_key'}).encode()).hexdigest()
    ledger=shipment_store()
    previous=ledger.existing(req.request_key,signature)
    if previous:return previous
    # 계산 중 원본이 교체되는 것을 막고 확정 당시 결과를 저장
    with STORE.lock:
        result,_=extraction(req)
        return ledger.confirm(req.request_key,signature,req.yard,result['rows'])

@app.get('/api/shipments')
def list_shipments(q:str='',yard:str='',status:str='',date_from:str='',date_to:str=''):
    if status and status not in STATUSES:raise ValueError('출고 상태를 확인하세요.')
    rows=shipment_store().rows(q,yard,status,date_from,date_to)
    return {'rows':rows,'headers':HEADERS+['공급가액','부가세','합계금액'],'count':len(rows),'quantity':sum(r['values'][4] or 0 for r in rows),'statuses':STATUSES}

class ShipmentUpdate(BaseModel):
    ids:list[int]=Field(min_length=1,max_length=20000)
    status:Literal['pending','shortage','erp_completed']|None=None
    memo:str|None=Field(default=None,max_length=2000)

@app.post('/api/shipments/status')
def update_shipments(req:ShipmentUpdate):
    return {'updated':shipment_store().update(req.ids,req.status,req.memo)}

@app.get('/api/shipments/download')
def download_shipments(q:str='',yard:str='',status:str='',date_from:str='',date_to:str=''):
    rows=shipment_store().rows(q,yard,status,date_from,date_to)
    w=Workbook();w.remove(w.active)
    s=styled_sheet(w,'출고내역',['확정일시','조선소','상태',*HEADERS,'공급가액','부가세','합계금액','메모'],[[r['created'],r['yard'],STATUSES[r['status']],*r['values'],r['memo']] for r in rows])
    from openpyxl.styles import PatternFill
    for i,r in enumerate(rows,2):
        color={'shortage':'FF0000','erp_completed':'FFFF00'}.get(r['status'])
        if color:
            for c in s[i]:c.fill=PatternFill('solid',fgColor=color)
        for col in [8,10,16,17,18]:
            value=s.cell(i,col).value
            s.cell(i,col).number_format='#,##0.########' if col in (8,10) and isinstance(value,(int,float)) and value%1 else '#,##0'
    # 같은 확정 묶음 안의 연속 품목별 합계만 병합합니다.
    start=2
    for i,r in enumerate(rows):
        group=(r['batch_id'],r['yard'],product_key(r['values']),r['values'][6])
        next_group=None if i==len(rows)-1 else (rows[i+1]['batch_id'],rows[i+1]['yard'],product_key(rows[i+1]['values']),rows[i+1]['values'][6])
        if group!=next_group:
            if i+2>start:s.merge_cells(start_row=start,start_column=10,end_row=i+2,end_column=10)
            start=i+3
    out=io.BytesIO();w.save(out)
    return excel(out.getvalue(),'shipment-history.xlsx')


# 선택 행·야드 설정·기존 출고리스트 가져오기 API
from .advanced import register_advanced
register_advanced(app, lambda: STORE, current, metadata, bytes_of, excel)
from .management import register_management
register_management(app,lambda: STORE,current,metadata,bytes_of,excel)
