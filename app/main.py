from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import HTMLResponse, Response
from pathlib import Path
from pydantic import BaseModel, Field
from typing import Literal
import zipfile
from copy import deepcopy
import io, logging
from .store import Store
from .files import read_book, read_raw
from .service import initialize, replace_raw, rebuild, filter_orders, timestamp, TARGETS
from .engine import extract, HEADERS, ADAPTERS, text
from .exporting import export_result, styled_sheet
from openpyxl import Workbook

ROOT=Path(__file__).parent
STORE=Store(ROOT.parent/'data/workspace.sqlite3')
app=FastAPI(title='선일 수주관리',version='2.0')

@app.exception_handler(ValueError)
async def value_error(request,exc):
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=400,content={'detail':str(exc)})

@app.exception_handler(zipfile.BadZipFile)
async def bad_zip(request,exc):
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=400,content={'detail':'압축 또는 엑셀 파일을 읽지 못했습니다. 파일이 손상되지 않았는지 확인하세요.'})

@app.get('/',response_class=HTMLResponse)
def home():return (ROOT/'static/index.html').read_text()
@app.get('/favicon.ico',include_in_schema=False)
def favicon():return Response(status_code=204)

def current():
    state=STORE.read()
    if state is None:raise ValueError('업데이트 탭에서 최초 기준 ZIP을 등록하세요.')
    return state

def metadata(state):
    if state is None:return {'initialized':False,'revision':0,'yards':[],'counts':{},'updates':{},'history':[],'warnings':[],'unclassified':[]}
    counts={n:sum(r['yard']==n for r in state['orders']) for n in [*ADAPTERS,'중소']}
    return {'initialized':True,'revision':state['revision'],'yards':list(counts),'counts':counts,'erp_count':len(state['erp']),'updates':state['updates'],'history':state.get('history',[])[-12:][::-1],'warnings':state['warnings'],'unclassified':state['unclassified']}

@app.get('/api/state')
def state():return metadata(STORE.read())

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
    yard:Literal['HMD','HHI','HSHI','중소']
    criteria:list[Criterion]=Field(default_factory=list,max_length=5000)
    revision:int

def extraction(req):
    state=current()
    if req.revision!=state['revision']:raise ValueError('자료가 변경됐습니다. 최신 자료로 다시 추출하세요.')
    return extract(state['book'],state['erp'],req.yard,[{'por':c.por.strip(),'block':c.block.strip()} for c in req.criteria]),state

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
