"""확정 당시 자료를 보관하는 누적 출고 원장. RAW 전체 교체와 독립."""
import json
import sqlite3
import uuid
from datetime import datetime, timezone, timedelta

STATUSES={'pending':'출고 대기','shortage':'재고 부족 · 출고 보류','erp_completed':'ERP 출고 완료'}

class Shipments:
    def __init__(self,path):
        self.path=path
        with sqlite3.connect(self.path) as db:
            db.execute('CREATE TABLE IF NOT EXISTS shipment_batches (id TEXT PRIMARY KEY, request_key TEXT UNIQUE NOT NULL, signature TEXT NOT NULL, created TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS shipments (id INTEGER PRIMARY KEY AUTOINCREMENT, batch_id TEXT NOT NULL, yard TEXT NOT NULL, status TEXT NOT NULL, memo TEXT NOT NULL DEFAULT \'\', payload TEXT NOT NULL)')
    def existing(self,key,signature):
        with sqlite3.connect(self.path) as db:
            r=db.execute('SELECT id,signature FROM shipment_batches WHERE request_key=?',(key,)).fetchone()
            if r:
                if r[1]!=signature:raise ValueError('이미 사용된 확정 요청입니다. 다시 추출한 뒤 확정하세요.')
                count=db.execute('SELECT COUNT(*) FROM shipments WHERE batch_id=?',(r[0],)).fetchone()[0]
                return {'batch_id':r[0],'count':count,'inserted':False}
    def confirm(self,key,signature,yard,records):
        if not records:raise ValueError('확정할 추출 결과가 없습니다.')
        with sqlite3.connect(self.path) as db:
            db.execute('BEGIN IMMEDIATE')
            prior=db.execute('SELECT id,signature FROM shipment_batches WHERE request_key=?',(key,)).fetchone()
            if prior:
                if prior[1]!=signature:raise ValueError('이미 사용된 확정 요청입니다. 다시 추출하세요.')
                count=db.execute('SELECT COUNT(*) FROM shipments WHERE batch_id=?',(prior[0],)).fetchone()[0]
                return {'batch_id':prior[0],'count':count,'inserted':False}
            batch=uuid.uuid4().hex;stamp=datetime.now(timezone(timedelta(hours=9))).isoformat()
            db.execute('INSERT INTO shipment_batches VALUES(?,?,?,?)',(batch,key,signature,stamp))
            db.executemany('INSERT INTO shipments(batch_id,yard,status,memo,payload) VALUES(?,?,?,?,?)',[(batch,yard,'pending','',json.dumps(r,ensure_ascii=False)) for r in records])
            return {'batch_id':batch,'count':len(records),'inserted':True}
    def rows(self,q='',yard='',status='',date_from='',date_to=''):
        with sqlite3.connect(self.path) as db:
            rows=db.execute('SELECT s.id,s.batch_id,s.yard,s.status,s.memo,s.payload,b.created FROM shipments s JOIN shipment_batches b ON b.id=s.batch_id ORDER BY s.id').fetchall()
        out=[]
        for id,batch,y,s,memo,payload,created in rows:
            if yard and y!=yard:continue
            if status and s!=status:continue
            if date_from and created[:10]<date_from:continue
            if date_to and created[:10]>date_to:continue
            rec=json.loads(payload)
            if q and q.casefold() not in ' '.join(str(v or '') for v in [y,memo,*rec['values']]).casefold():continue
            out.append({'id':id,'batch_id':batch,'yard':y,'status':s,'memo':memo,'created':created,**rec})
        return out
    def update(self,ids,status=None,memo=None):
        if not ids:raise ValueError('출고내역을 선택하세요.')
        if status is not None and status not in STATUSES:raise ValueError('출고 상태가 올바르지 않습니다.')
        if status is None and memo is None:raise ValueError('변경할 상태 또는 메모를 입력하세요.')
        ids=sorted(set(ids))
        with sqlite3.connect(self.path) as db:
            db.execute('BEGIN IMMEDIATE')
            placeholders=','.join('?' for _ in ids)
            count=db.execute(f'SELECT COUNT(*) FROM shipments WHERE id IN ({placeholders})',ids).fetchone()[0]
            if count!=len(ids):raise ValueError('일부 출고내역을 찾지 못했습니다. 다시 조회하세요.')
            fields=[];values=[]
            if status is not None:fields.append('status=?');values.append(status)
            if memo is not None:fields.append('memo=?');values.append(memo)
            db.execute(f'UPDATE shipments SET {", ".join(fields)} WHERE id IN ({placeholders})',values+ids)
        return count
