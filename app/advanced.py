"""선택 행 조회, 설정형 야드, 기존 출고 원장 가져오기."""
import io,json,hashlib
from copy import deepcopy
from fastapi import UploadFile,File,Form
from pydantic import BaseModel,Field
from openpyxl import load_workbook, Workbook
from .engine import HEADERS,ERP_HEADERS,ADAPTERS,convert_erp,text,number,day
from .yards import YardConfig,configured_source
from .service import rebuild,timestamp,norm
from .files import read_raw
from .exporting import styled_sheet
from .shipments import Shipments

class RowExport(BaseModel):
    revision:int
    ids:list[str]=Field(min_length=1,max_length=50000)

def register_advanced(app,get_store,current,metadata,bytes_of,excel):
    @app.get('/api/orders/all')
    def all_orders():
        state=current()
        return {'revision':state['revision'],'headers':['조선소',*HEADERS],'rows':[{'id':f"{r['yard']}:{i}",**r} for i,r in enumerate(state['orders'])]}

    @app.post('/api/orders/ids-download')
    def export_ids(req:RowExport):
        state=current()
        if state['revision']!=req.revision:raise ValueError('자료가 변경됐습니다. 다시 조회하세요.')
        index={f"{r['yard']}:{i}":r for i,r in enumerate(state['orders'])}
        if any(id not in index for id in req.ids):raise ValueError('일부 행이 없습니다. 다시 조회하세요.')
        w=Workbook();w.remove(w.active)
        styled_sheet(w,'수주현황',['조선소',*HEADERS],[[index[id]['yard'],*index[id]['values']] for id in dict.fromkeys(req.ids)])
        out=io.BytesIO();w.save(out);return excel(out.getvalue(),'orders.xlsx')

    @app.get('/api/yards')
    def yards():
        state=current();return {'configs':state.get('yards',{}),'sheets':list(state['book']),'erp_headers':ERP_HEADERS,'revision':state['revision']}

    async def sample(name,file):
        if file and file.filename:return read_raw(await bytes_of(file),file.filename,name)
        rows=current()['book'].get(name)
        if not rows:raise ValueError('샘플 RAW를 업로드하거나 최초 엑셀에 있는 시트명을 입력하세요.')
        return rows

    @app.post('/api/yards/columns')
    async def columns(name:str=Form(...),header_row:int=Form(1),file:UploadFile|None=File(None)):
        if not 1<=header_row<=20:raise ValueError('머리글 행은 1~20 범위입니다.')
        rows=await sample(name.strip(),file)
        if len(rows)<header_row:raise ValueError('머리글 행을 확인하세요.')
        return {'headers':[text(v) for v in rows[header_row-1]],'preview':rows[header_row:header_row+5],'total':max(0,len(rows)-header_row)}

    async def prepare(config,file):
        cfg=YardConfig.model_validate_json(config).checked()
        rows=await sample(cfg['name'],file)
        if len(rows)<cfg['header_row']:raise ValueError('머리글 위치가 원본 범위를 벗어났습니다.')
        used=[*cfg['match_cols'],cfg['qty'],*[v for v in [cfg.get('hull'),cfg.get('block'),cfg.get('title'),cfg.get('due'),cfg.get('destination')] if v]]
        if max(used)>len(rows[cfg['header_row']-1]):raise ValueError('선택한 열이 샘플 머리글 범위를 벗어났습니다.')
        old=current();book={**old['book'],cfg['name']:rows}
        output,warnings,stats=configured_source(book,old['erp'],cfg['name'],cfg)
        return cfg,rows,output,warnings,stats

    @app.post('/api/yards/preview')
    async def yard_preview(config:str=Form(...),file:UploadFile|None=File(None)):
        cfg,rows,output,warnings,stats=await prepare(config,file)
        return {'headers':HEADERS,'rows':output[:20],'warnings':warnings,'stats':stats}

    @app.post('/api/yards/save')
    async def yard_save(config:str=Form(...),revision:int=Form(...),file:UploadFile|None=File(None)):
        cfg,rows,output,warnings,stats=await prepare(config,file)
        old=current();book={**old['book'],cfg['name']:rows};configs={**old.get('yards',{}),cfg['name']:cfg};stamp=timestamp()
        updated=rebuild({**old,'book':book,'yards':configs,'updates':{**old['updates'],cfg['name']:stamp},'history':(old.get('history',[])+[{'at':stamp,'target':cfg['name']+' 야드 매칭 설정 저장'}])[-100:]})
        return metadata(get_store().commit(updated,revision))

    @app.post('/api/shipments/import-preview')
    async def import_preview(file:UploadFile=File(...),yard:str=Form('기존자료')):
        data=await bytes_of(file);records,notes=parse_shipment_file(data,yard)
        return {'count':len(records),'statuses':{s:sum(r['import_status']==s for r in records) for s in ('pending','shortage','erp_completed')},'headers':HEADERS+['공급가액','부가세','합계금액'],'rows':[r['values'] for r in records[:10]],'warnings':notes}

    @app.post('/api/shipments/import')
    async def import_history(file:UploadFile=File(...),yard:str=Form('기존자료'),confirmed:bool=Form(False)):
        if not confirmed:raise ValueError('미리보기 확인 후 가져오기를 실행하세요.')
        data=await bytes_of(file);records,notes=parse_shipment_file(data,yard)
        key='import-'+hashlib.sha256(data).hexdigest();ledger=Shipments(get_store().path)
        signature=hashlib.sha256((key+yard).encode()).hexdigest()
        return ledger.import_records(key,signature,yard,records)

def parse_shipment_file(data,yard):
    from .files import checked_zip
    checked_zip(data).close()
    try:w=load_workbook(io.BytesIO(data),data_only=True)
    except Exception:raise ValueError('기존 출고리스트는 XLSX 파일로 업로드하세요.')
    result=[];notes=[]
    try:
        for sheet in w:
            headers=[norm(sheet.cell(1,c).value) for c in range(1,16)]
            expected=[norm(v) for v in HEADERS+['공급가액','부가세','합계금액']]
            if not any(headers):continue
            if headers!=expected:raise ValueError(f'{sheet.title}: 첫 행의 A~O 머리글이 기존 출고리스트 형식과 다릅니다.')
            merged_totals={}
            for group in sheet.merged_cells.ranges:
                if group.min_col==7 and group.max_col==7:
                    for rn in range(group.min_row,group.max_row+1):merged_totals[rn]=sheet.cell(group.min_row,7).value
            for row in sheet.iter_rows(min_row=2,max_col=15):
                vals=[c.value for c in row]
                if not any(text(vals[i]) for i in (0,1,3,5)):continue
                n=row[0].row
                if n in merged_totals:vals[6]=merged_totals[n]
                for i in [4,6,9,12,13,14]:vals[i]=number(vals[i])
                for i in [8,11]:vals[i]=day(vals[i])
                vals=[text(v) if i in (0,1,2,3,5,7,10) else v for i,v in enumerate(vals)]
                colors={str(c.fill.fgColor.rgb)[-6:].upper() for c in row if c.fill.patternType=='solid' and c.fill.fgColor.type=='rgb'}
                if 'FF0000' in colors and 'FFFF00' in colors:raise ValueError(f'{sheet.title} {n}행에 빨강과 노랑이 함께 있습니다. 상태를 하나로 정리하세요.')
                status='shortage' if 'FF0000' in colors else 'erp_completed' if 'FFFF00' in colors else 'pending'
                result.append({'yard':yard.strip() or '기존자료','values':vals,'unmatched':False,'import_status':status})
    finally:w.close()
    if not result:raise ValueError('가져올 출고내역이 없습니다.')
    notes.append('기존 15개 열의 값은 그대로 보관합니다. H(SET)·K(납품처)에 별도 관리 번호가 있어도 재해석하지 않습니다.')
    return result,notes
