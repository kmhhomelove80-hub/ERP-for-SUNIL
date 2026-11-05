"""ERP 출고 완료 자료를 주차별 파일로 검증 저장한 뒤 활성 목록에서 제외."""
import hashlib,io,json,os,sqlite3,uuid,re
from pathlib import Path
from datetime import datetime,timezone,timedelta,date
from openpyxl import Workbook,load_workbook
from openpyxl.styles import PatternFill
from .shipments import Shipments
from .engine import HEADERS,product_key
from .exporting import styled_sheet

KST=timezone(timedelta(hours=9))
def now():return datetime.now(KST)
def fingerprint(rows):return hashlib.sha256(json.dumps(rows,ensure_ascii=False,sort_keys=True,default=str).encode()).hexdigest()

class Closing:
 def __init__(self,path):
  self.path=Path(path);self.ledger=Shipments(path)
  with sqlite3.connect(path) as db:
   db.execute('CREATE TABLE IF NOT EXISTS preferences (key TEXT PRIMARY KEY,value TEXT NOT NULL)')
   db.execute('CREATE TABLE IF NOT EXISTS weekly_archives (id TEXT PRIMARY KEY,request_key TEXT UNIQUE,week TEXT NOT NULL,created TEXT NOT NULL,path TEXT NOT NULL,checksum TEXT NOT NULL,count INTEGER NOT NULL,quantity REAL NOT NULL)')
 def root(self):
  with sqlite3.connect(self.path) as db:
   row=db.execute("SELECT value FROM preferences WHERE key='backup_root'").fetchone()
   if row:return Path(row[0])
  # 프로그램 버전 폴더와 분리된 사용자 문서 아래가 기본값. 실제 저장은 마감 시 수행.
  return Path.home()/'Documents'/'SUNIL_Backup'/'출고내역'
 def set_root(self,path):
  p=Path(path).expanduser()
  if not p.is_absolute():raise ValueError('백업 경로는 절대 경로로 입력하세요.')
  p=p.resolve();p.mkdir(parents=True,exist_ok=True)
  with sqlite3.connect(self.path) as db:db.execute("INSERT INTO preferences VALUES('backup_root',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(str(p),))
  return str(p)
 def list(self):
  with sqlite3.connect(self.path) as db:return [{'id':i,'week':w,'created':c,'path':p,'count':n,'quantity':q} for i,w,c,p,n,q in db.execute('SELECT id,week,created,path,count,quantity FROM weekly_archives ORDER BY rowid DESC')]
 def existing(self,key):
  with sqlite3.connect(self.path) as db:r=db.execute('SELECT id,week,path,count FROM weekly_archives WHERE request_key=?',(key,)).fetchone()
  return {'id':r[0],'week':r[1],'path':r[2],'count':r[3],'inserted':False} if r else None
 def file(self,id):
  with sqlite3.connect(self.path) as db:r=db.execute('SELECT path,checksum FROM weekly_archives WHERE id=?',(id,)).fetchone()
  if not r:raise ValueError('백업 기록이 없습니다.')
  p=Path(r[0])
  if not p.is_file():raise ValueError('백업 파일을 찾지 못했습니다. 저장 경로를 확인하세요.')
  data=p.read_bytes()
  if hashlib.sha256(data).hexdigest()!=r[1]:raise ValueError('백업 파일 내용이 변경됐습니다. 원본 백업을 확인하세요.')
  return p,data
 def close(self,key,week,ids,expected_fingerprint,expected_root):
  existing=self.existing(key)
  if existing:return existing
  if not re.fullmatch(r'\d{4}-W\d{2}',week):raise ValueError('주차 형식은 YYYY-Www입니다.')
  year,wn=map(int,week.replace('-W','-').split('-'))
  try:date.fromisocalendar(year,wn,1)
  except ValueError:raise ValueError('유효한 주차를 선택하세요.')
  root=self.root()
  if str(root)!=expected_root:raise ValueError('백업 경로가 변경됐습니다. 마감 미리보기를 다시 실행하세요.')
  file=None;tmp=None;owned_file=False;owned_tmp=False
  try:
   with sqlite3.connect(self.path) as db:
    db.execute('BEGIN IMMEDIATE')
    active={r['id']:r for r in self.ledger.rows()}
    if not ids or any(i not in active or active[i]['status']!='erp_completed' for i in ids):raise ValueError('마감 대상 상태가 변경됐습니다. 다시 미리보기하세요.')
    records=[active[i] for i in ids]
    if fingerprint(records)!=expected_fingerprint:raise ValueError('마감 대상 수량·메모·상태가 변경됐습니다. 다시 미리보기하세요.')
    # 파일에는 마감 대상 행들만의 합계를 기록
    totals={}
    for r in records:
     k=(r['batch_id'],r['yard'],product_key(r['values']));totals[k]=totals.get(k,0)+(r['values'][4] or 0)
    for r in records:r['values'][6]=totals[(r['batch_id'],r['yard'],product_key(r['values']))]
    stamp=now();id=uuid.uuid4().hex
    folder=root/str(year)/week;folder.mkdir(parents=True,exist_ok=True)
    file=folder/f'ERP출고완료_{week}_{stamp:%Y%m%d_%H%M%S}_{id}.xlsx';tmp=file.with_suffix('.tmp')
    w=Workbook();w.remove(w.active)
    s=styled_sheet(w,'ERP출고완료',['확정일시','조선소','상태',*HEADERS,'공급가액','부가세','합계금액','메모','마감주차','마감일시'],[[r['created'],r['yard'],'ERP 출고 완료',*r['values'],r['memo'],week,stamp.isoformat()] for r in records])
    for row in s.iter_rows(min_row=2):
     for c in row:c.fill=PatternFill('solid',fgColor='FFFF00')
    out=io.BytesIO();w.save(out);data=out.getvalue()
    with open(tmp,'xb') as f:
     owned_tmp=True;f.write(data);f.flush();os.fsync(f.fileno())
    verified=tmp.read_bytes()
    check=load_workbook(io.BytesIO(verified),read_only=True)
    if check['ERP출고완료'].max_row!=len(records)+1:check.close();raise ValueError('백업 행 수 검증에 실패했습니다.')
    check.close()
    if verified!=data:raise ValueError('백업 파일 저장 검증에 실패했습니다.')
    if file.exists():raise FileExistsError('동일 이름의 백업 파일이 이미 있습니다. 다시 마감하세요.')
    os.replace(tmp,file);owned_tmp=False;owned_file=True
    checksum=hashlib.sha256(data).hexdigest();qty=sum(r['values'][4] or 0 for r in records)
    db.execute('INSERT INTO weekly_archives VALUES(?,?,?,?,?,?,?,?)',(id,key,week,stamp.isoformat(),str(file),checksum,len(records),qty))
    for i in ids:db.execute('UPDATE shipments SET archived_id=? WHERE id=?',(id,i))
   return {'id':id,'week':week,'path':str(file),'count':len(records),'inserted':True}
  except Exception:
   if owned_tmp and tmp and tmp.exists():tmp.unlink()
   # 저장 성공 후 DB 반영 실패 시 파일만 남아 중복 혼동이 없도록 정리. 원장 트랜잭션은 롤백.
   if owned_file and file and file.exists() and not self.existing(key):file.unlink()
   raise
