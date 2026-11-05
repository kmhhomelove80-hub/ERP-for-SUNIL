"""원본 매칭·수량/삭제·복구·주간 마감 API."""
import time,secrets,sqlite3,os
from pathlib import Path
from fastapi import UploadFile,File,Form
from pydantic import BaseModel,Field
from .raw_mapping import plan_raw,materialize
from .files import read_raw
from .service import replace_raw,rebuild,timestamp
from .order_edits import quantity
from .shipments import Shipments
from .closing import Closing,fingerprint,now

class RawApply(BaseModel):
 token:str
 revision:int
 mapping:dict[str,int|None]=Field(default_factory=dict)
class Restore(BaseModel):
 snapshot:int
 revision:int
class OrderEdit(BaseModel):
 ids:list[str]=Field(min_length=1,max_length=50000)
 revision:int
 quantity:float|None=None
 delete:bool=False
class ShipmentEdit(BaseModel):
 ids:list[int]=Field(min_length=1,max_length=20000)
 quantity:float|None=None
 delete:bool=False
class RootSetting(BaseModel):path:str=Field(min_length=1,max_length=1000)
class ClosePreview(BaseModel):
 week:str
 q:str=''
 yard:str=''
 date_from:str=''
 date_to:str=''
class CloseCommit(BaseModel):token:str
class FolderOpen(BaseModel):archive_id:str|None=None

def register_management(app,get_store,current,metadata,bytes_of,excel):
 staged={};closings={}
 def stage(data,cache):
  for key in list(cache):
   if cache[key]['expires']<time.time():del cache[key]
  if len(cache)>=5:cache.pop(next(iter(cache)))
  token=secrets.token_urlsafe(24);cache[token]={**data,'expires':time.time()+900};return token
 def closing():return Closing(get_store().path)

 @app.post('/api/raw/preview')
 async def raw_preview(target:str=Form(...),revision:int=Form(...),header_row:int=Form(0),file:UploadFile=File(...)):
  old=current()
  if revision!=old['revision']:raise ValueError('자료가 변경됐습니다. 새로고침하세요.')
  rows=read_raw(await bytes_of(file),file.filename or '',target);plan=plan_raw(old,target,rows,header_row)
  token=stage({'target':target,'revision':revision,'rows':rows,'plan':plan},staged)
  return {**plan,'token':token}
 @app.post('/api/raw/validate')
 def raw_validate(req:RawApply):
  item=staged.get(req.token)
  if not item or item['expires']<time.time():raise ValueError('미리보기가 만료됐습니다.')
  old=current()
  if old['revision']!=req.revision or item['revision']!=req.revision:raise ValueError('자료가 변경됐습니다. 다시 업로드하세요.')
  rows,_=materialize(old,item['target'],item['rows'],item['plan'],req.mapping)
  updated=replace_raw(old,item['target'],rows)
  from .engine import HEADERS,ERP_HEADERS
  preview=([r[:17] for r in updated['erp'][:10]] if item['target']=='ERP' else [r['values'] for r in updated['orders'] if r['yard']==item['target']][:10])
  count=(len(updated['erp']) if item['target']=='ERP' else sum(r['yard']==item['target'] for r in updated['orders']))
  return {'headers':ERP_HEADERS if item['target']=='ERP' else HEADERS,'rows':preview,'count':count,'warnings':updated['warnings'][:8]}
 @app.post('/api/raw/apply')
 def raw_apply(req:RawApply):
  item=staged.get(req.token)
  if not item or item['expires']<time.time():raise ValueError('업로드 미리보기가 만료됐습니다. 파일을 다시 올리세요.')
  old=current()
  if req.revision!=old['revision'] or item['revision']!=req.revision:raise ValueError('자료가 변경됐습니다. 다시 미리보기하세요.')
  rows,aliases=materialize(old,item['target'],item['rows'],item['plan'],req.mapping)
  updated=replace_raw(old,item['target'],rows)
  updated['raw_aliases']={**old.get('raw_aliases',{}),item['target']:aliases}
  saved=get_store().commit(updated,req.revision);del staged[req.token]
  return metadata(saved)
 @app.get('/api/state/backups')
 def backups():return {'rows':get_store().backups(),'revision':current()['revision']}
 @app.post('/api/state/restore')
 def restore(req:Restore):return metadata(get_store().restore(req.snapshot,req.revision))
 @app.post('/api/orders/edit')
 def edit_orders(req:OrderEdit):
  old=current()
  if req.revision!=old['revision']:raise ValueError('자료가 변경됐습니다. 다시 조회하세요.')
  index={f"{r['yard']}:{i}":r for i,r in enumerate(old['orders'])}
  ids=list(dict.fromkeys(req.ids))
  if any(id not in index for id in ids):raise ValueError('선택 행이 없습니다. 다시 조회하세요.')
  qty=quantity(req.quantity) if not req.delete else None
  edits={**old.get('manual_orders',{})}
  for id in ids:
   row=index[id];key=row['rowkey'];edits[key]={**edits.get(key,{}),**({'deleted':True} if req.delete else {'quantity':qty})}
  action=('수주현황 행 삭제' if req.delete else '수주현황 수량 수정')+f' {len(ids)}행'
  updated=rebuild({**old,'manual_orders':edits,'history':(old.get('history',[])+[{'at':timestamp(),'target':action}])[-100:]})
  return metadata(get_store().commit(updated,req.revision))
 @app.post('/api/shipments/edit')
 def edit_shipments(req:ShipmentEdit):return {'updated':Shipments(get_store().path).edit(req.ids,req.quantity,req.delete)}
 @app.post('/api/shipments/undo')
 def undo_shipment():return {'action':Shipments(get_store().path).undo()}
 @app.get('/api/backup')
 def backup_settings():
  c=closing();iso=now().date().isocalendar()
  return {'path':str(c.root()),'week':f'{iso.year}-W{iso.week:02d}','rows':c.list()}
 @app.post('/api/backup/path')
 def backup_path(req:RootSetting):return {'path':closing().set_root(req.path)}
 @app.post('/api/backup/open')
 def open_folder(req:FolderOpen):
  c=closing();path=c.file(req.archive_id)[0].parent if req.archive_id else c.root();path.mkdir(parents=True,exist_ok=True)
  if os.name=='nt':os.startfile(str(path));return {'opened':True,'path':str(path)}
  return {'opened':False,'path':str(path),'message':'Windows에서는 탐색기가 열립니다. 현재 환경에서는 표시된 경로를 사용하세요.'}
 @app.get('/api/backup/download/{id}')
 def backup_download(id:str):
  p,data=closing().file(id);return excel(data,'weekly-close.xlsx')
 @app.post('/api/close/preview')
 def close_preview(req:ClosePreview):
  c=closing();rows=c.ledger.rows(req.q,req.yard,'erp_completed',req.date_from,req.date_to)
  if not rows:raise ValueError('필터 조건에 맞는 ERP 출고 완료 내역이 없습니다.')
  token=stage({'week':req.week,'ids':[r['id'] for r in rows],'fingerprint':fingerprint(rows),'root':str(c.root())},closings)
  return {'token':token,'week':req.week,'count':len(rows),'quantity':sum(r['values'][4] or 0 for r in rows),'path':str(c.root()),'preview':rows[:10]}
 @app.post('/api/close/commit')
 def close_commit(req:CloseCommit):
  c=closing();prior=c.existing(req.token)
  if prior:return prior
  info=closings.get(req.token)
  if not info or info['expires']<time.time():raise ValueError('마감 미리보기가 만료됐습니다. 다시 조회하세요.')
  return c.close(req.token,info['week'],info['ids'],info['fingerprint'],info['root'])
