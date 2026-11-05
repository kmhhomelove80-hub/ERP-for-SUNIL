"""확정 당시 자료를 보관하는 누적 출고 원장. RAW 전체 교체와 독립."""
import json
import sqlite3
import uuid
from .engine import product_key
from datetime import datetime, timezone, timedelta

STATUSES={'pending':'출고 대기','shortage':'재고 부족 · 출고 보류','erp_completed':'ERP 출고 완료'}

class Shipments:
    def __init__(self,path):
        self.path=path
        with sqlite3.connect(self.path) as db:
            db.execute('CREATE TABLE IF NOT EXISTS shipment_batches (id TEXT PRIMARY KEY, request_key TEXT UNIQUE NOT NULL, signature TEXT NOT NULL, created TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS shipment_changes (id INTEGER PRIMARY KEY AUTOINCREMENT, created TEXT NOT NULL, kind TEXT NOT NULL, before_json TEXT NOT NULL, undone INTEGER NOT NULL DEFAULT 0)')
            db.execute('CREATE TABLE IF NOT EXISTS shipments (id INTEGER PRIMARY KEY AUTOINCREMENT, batch_id TEXT NOT NULL, yard TEXT NOT NULL, status TEXT NOT NULL, memo TEXT NOT NULL DEFAULT \'\', payload TEXT NOT NULL)')
            columns={r[1] for r in db.execute('PRAGMA table_info(shipments)')}
            for column,definition in [('deleted','INTEGER NOT NULL DEFAULT 0'),('archived_id','TEXT')]:
                if column not in columns:db.execute(f'ALTER TABLE shipments ADD COLUMN {column} {definition}')
    def _capture(self,db,ids,kind):
        p=','.join('?' for _ in ids)
        db.row_factory=sqlite3.Row
        rows=[dict(r) for r in db.execute(f'SELECT * FROM shipments WHERE id IN ({p}) AND archived_id IS NULL',ids)]
        db.row_factory=None
        if len(rows)!=len(set(ids)):raise ValueError('일부 항목이 마감됐거나 없습니다. 다시 조회하세요.')
        db.execute('INSERT INTO shipment_changes(created,kind,before_json) VALUES(?,?,?)',(datetime.now(timezone(timedelta(hours=9))).isoformat(),kind,json.dumps(rows,ensure_ascii=False)))
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
            db.executemany('INSERT INTO shipments(batch_id,yard,status,memo,payload) VALUES(?,?,?,?,?)',[(batch,r.get('yard',yard),r.get('import_status','pending'),'',json.dumps(r,ensure_ascii=False)) for r in records])
            return {'batch_id':batch,'count':len(records),'inserted':True}
    def rows(self,q='',yard='',status='',date_from='',date_to=''):
        with sqlite3.connect(self.path) as db:
            rows=db.execute('SELECT s.id,s.batch_id,s.yard,s.status,s.memo,s.payload,b.created FROM shipments s JOIN shipment_batches b ON b.id=s.batch_id WHERE s.deleted=0 AND s.archived_id IS NULL ORDER BY b.rowid DESC, s.id ASC').fetchall()
        totals={}
        for _,batch,y,_,_,payload,_ in rows:
            rec=json.loads(payload);key=(batch,y,product_key(rec['values']));totals[key]=totals.get(key,0)+(rec['values'][4] or 0)
        out=[]
        for id,batch,y,s,memo,payload,created in rows:
            if yard and y!=yard:continue
            if status and s!=status:continue
            if date_from and created[:10]<date_from:continue
            if date_to and created[:10]>date_to:continue
            rec=json.loads(payload);rec['values'][6]=totals[(batch,y,product_key(rec['values']))]
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
            count=db.execute(f'SELECT COUNT(*) FROM shipments WHERE id IN ({placeholders}) AND deleted=0 AND archived_id IS NULL',ids).fetchone()[0]
            if count!=len(ids):raise ValueError('일부 출고내역을 찾지 못했습니다. 다시 조회하세요.')
            self._capture(db,ids,'상태·메모 변경')
            fields=[];values=[]
            if status is not None:fields.append('status=?');values.append(status)
            if memo is not None:fields.append('memo=?');values.append(memo)
            db.execute(f'UPDATE shipments SET {", ".join(fields)} WHERE id IN ({placeholders})',values+ids)
        return count

    def import_records(self,key,signature,yard,records):
        if any(r.get('import_status') not in STATUSES for r in records):raise ValueError('기존 내역의 상태를 확인하세요.')
        return self.confirm(key,signature,yard,records)

    def edit(self,ids,qty=None,delete=False):
        from .order_edits import quantity
        ids=sorted(set(ids))
        if not ids:raise ValueError('항목을 선택하세요.')
        q=quantity(qty) if not delete else None
        with sqlite3.connect(self.path) as db:
            db.execute('BEGIN IMMEDIATE');self._capture(db,ids,'행 삭제' if delete else '수량 수정')
            for id in ids:
                row=db.execute('SELECT payload,deleted FROM shipments WHERE id=?',(id,)).fetchone()
                if row[1]:raise ValueError('이미 삭제된 항목입니다.')
                if delete:db.execute('UPDATE shipments SET deleted=1 WHERE id=?',(id,))
                else:
                    rec=json.loads(row[0]);rec['values'][4]=q
                    db.execute('UPDATE shipments SET payload=? WHERE id=?',(json.dumps(rec,ensure_ascii=False),id))
        return len(ids)
    def undo(self):
        with sqlite3.connect(self.path) as db:
            db.execute('BEGIN IMMEDIATE')
            action=db.execute('SELECT id,kind,before_json FROM shipment_changes WHERE undone=0 ORDER BY id DESC LIMIT 1').fetchone()
            if not action:raise ValueError('되돌릴 출고내역 작업이 없습니다.')
            rows=json.loads(action[2])
            for r in rows:
                current=db.execute('SELECT archived_id FROM shipments WHERE id=?',(r['id'],)).fetchone()
                if not current or current[0]:raise ValueError('해당 항목이 주간 마감됐습니다. 마감된 자료는 백업 파일에서 확인하세요.')
            for r in rows:db.execute('UPDATE shipments SET status=?,memo=?,payload=?,deleted=? WHERE id=?',(r['status'],r['memo'],r['payload'],r.get('deleted',0),r['id']))
            db.execute('UPDATE shipment_changes SET undone=1 WHERE id=?',(action[0],))
        return action[1]
