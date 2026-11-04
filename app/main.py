from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import HTMLResponse, Response
from pathlib import Path
from pydantic import BaseModel, Field
from threading import Lock
import io, time, secrets
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from .files import read_book, read_erp
from .classifications import ClassificationStore
from .engine import convert_erp, extract, ADAPTERS, ERP_HEADERS, HEADERS

app=FastAPI(title='선일 납품리스트')
SESSIONS={}; LOCK=Lock(); TTL=3600
ROOT=Path(__file__).parent
CLASSIFICATIONS=ClassificationStore()

@app.get('/',response_class=HTMLResponse)
def home():return (ROOT/'static/index.html').read_text()

async def upload_bytes(file):
    data=await file.read(25*1024*1024+1)
    if len(data)>25*1024*1024:raise ValueError('파일은 각각 25MB까지 업로드할 수 있습니다.')
    return data

@app.post('/api/upload')
async def upload(workbook:UploadFile=File(...),erp_file:UploadFile|None=File(None)):
    try:
        book=read_book(await upload_bytes(workbook),workbook.filename or '')
        external=read_erp(await upload_bytes(erp_file),erp_file.filename or '') if erp_file and erp_file.filename else None
        erp=convert_erp(book,external,CLASSIFICATIONS.all())
        if not erp:raise ValueError('처리할 ERP 데이터가 없습니다.')
        token=secrets.token_urlsafe(24)
        with LOCK:
            for k in list(SESSIONS):
                if SESSIONS[k]['expires']<time.time():del SESSIONS[k]
            if len(SESSIONS)>=8:raise ValueError('동시 작업 수가 많습니다. 잠시 후 다시 시도하세요.')
            SESSIONS[token]={'book':book,'external':external,'erp':erp,'expires':time.time()+TTL}
        return {'session':token,'erp_count':len(erp),'available':[n for n in [*ADAPTERS,'중소'] if n=='중소' or n in book],'sheets':list(book),'unclassified':sorted({r[0] for r in erp if r[1]=='미분류'}),'classifications':[{'customer':k,'name':v[0],'category':v[1]} for k,v in CLASSIFICATIONS.all().items()]}
    except (ValueError,KeyError,ImportError) as e:raise HTTPException(400,str(e))
    except Exception:raise HTTPException(400,'파일을 읽지 못했습니다. 지원 형식과 압축 파일 구성을 확인하세요.')

class Criteria(BaseModel):
    por:str=Field(default='',max_length=200)
    block:str=Field(default='',max_length=200)
class Request(BaseModel):
    session:str
    shipyard:str
    criteria:list[Criteria]=Field(default_factory=list,max_length=5000)

def process(req):
    with LOCK:
        s=SESSIONS.get(req.session)
        if not s or s['expires']<time.time():raise HTTPException(410,'작업이 만료되었습니다. 파일을 다시 올려 주세요.')
    try:return extract(s['book'],s['erp'],req.shipyard,[{'por':c.por.strip(),'block':c.block.strip()} for c in req.criteria]),s
    except (ValueError,KeyError) as e:raise HTTPException(400,str(e))

@app.post('/api/preview')
def preview(req:Request):
    result,_=process(req)
    return {k:v for k,v in result.items() if k not in ('source_rows','rows')} | {'rows':result['rows'][:300],'preview_limit':300}

def safe_value(v):
    # 문자열이 다운로드 파일에서 수식으로 해석되는 것 방지
    return "'"+v if isinstance(v,str) and v.startswith(('=','+','-','@')) else v

def styled_sheet(w,name,headers,rows):
    s=w.create_sheet(name);s.append(headers)
    for row in rows:s.append([safe_value(v) for v in row])
    s.freeze_panes='A2';s.auto_filter.ref=s.dimensions
    for c in s[1]:c.font=Font(name='맑은 고딕',bold=True,color='FFFFFF');c.fill=PatternFill('solid',fgColor='173D42')
    from openpyxl.utils import get_column_letter
    for j,h in enumerate(headers,1):s.column_dimensions[get_column_letter(j)].width=48 if h=='품명' else 18
    for row in s.iter_rows(min_row=2):
        for c in row:c.alignment=Alignment(vertical='center');c.font=Font(name='맑은 고딕',size=10)
    return s

@app.post('/api/download')
def download(req:Request):
    result,state=process(req);w=Workbook();w.remove(w.active)
    s=styled_sheet(w,'추출',result['headers'],[r['values'] for r in result['rows']])
    for i,rec in enumerate(result['rows'],2):
        if rec['unmatched']:
            for c in s[i]:c.fill=PatternFill('solid',fgColor='FFEBEB')
        for col in [5,7,13,14,15]:s.cell(i,col).number_format='#,##0'
    # 품목코드별 합계를 기존 M열과 동일하게 병합
    start=2;records=result['rows']
    for idx,rec in enumerate(records):
        end=idx+2
        if idx==len(records)-1 or str(rec['values'][5]).casefold()!=str(records[idx+1]['values'][5]).casefold():
            if end>start:s.merge_cells(start_row=start,start_column=7,end_row=end,end_column=7)
            start=end+1
    styled_sheet(w,req.shipyard,HEADERS,result['source_rows'])
    styled_sheet(w,'ERP변환',ERP_HEADERS,[r[:17] for r in state['erp']])
    styled_sheet(w,'처리안내',['항목','내용'],[['조선소',req.shipyard],['조건',str([c.model_dump() for c in req.criteria])],['행 통합 기준','POR·호선·블록·품명·품목코드 동일, 수량만 합산'],['금액','중복 POR 첫 번째 ERP 행 사용. 통합 시 첫 번째 금액 유지.']]+[['안내',v] for v in result['warnings']])
    out=io.BytesIO();w.save(out)
    return Response(out.getvalue(),media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',headers={'Content-Disposition':'attachment; filename="delivery-list.xlsx"'})


from typing import Literal
class Classification(BaseModel):
    customer:str=Field(min_length=1,max_length=200)
    name:str=Field(min_length=1,max_length=200)
    category:Literal['대형','중소']
class ClassificationRequest(BaseModel):
    session:str
    rows:list[Classification]=Field(min_length=1,max_length=500)

@app.post('/api/classifications')
def save_classifications(req:ClassificationRequest):
    with LOCK:
        state=SESSIONS.get(req.session)
        if not state or state['expires']<time.time():raise HTTPException(410,'작업이 만료되었습니다. 파일을 다시 올려 주세요.')
        mappings=CLASSIFICATIONS.all()
        rows=[]
        for r in req.rows:
            customer,name=r.customer.strip(),r.name.strip()
            if not customer or not name:raise HTTPException(400,'거래처명과 조선소명을 입력하세요.')
            mappings[customer]=(name,r.category)
            rows.append((customer,name,r.category))
        try:updated=convert_erp(state['book'],state.get('external'),mappings)
        except ValueError as e:raise HTTPException(400,str(e))
        CLASSIFICATIONS.save(rows)
        SESSIONS[req.session]={**state,'erp':updated}
    return {'erp_count':len(updated),'unclassified':sorted({r[0] for r in updated if r[1]=='미분류'})}
