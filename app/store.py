"""원본·기준정보·변환 결과를 SQLite에 원자적으로 저장합니다."""
import json
import sqlite3
import threading
from pathlib import Path
from datetime import date, datetime

class Store:
    def __init__(self,path):
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True)
        self.lock=threading.RLock();self.cache=None
        with sqlite3.connect(self.path) as db:
            db.execute('CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL, payload TEXT NOT NULL)')
            row=db.execute('SELECT revision,payload FROM state WHERE id=1').fetchone()
            if row:self.cache=json.loads(row[1]);self.cache['revision']=row[0]
    def read(self):
        with self.lock:return self.cache
    def commit(self,payload,expected):
        with self.lock:
            current=self.cache['revision'] if self.cache else 0
            if expected!=current:raise ValueError('다른 업데이트가 먼저 저장됐습니다. 새로고침 후 다시 시도하세요.')
            payload={**payload,'revision':current+1}
            encoded=json.dumps(payload,ensure_ascii=False,default=lambda v:v.isoformat() if isinstance(v,(date,datetime)) else str(v))
            with sqlite3.connect(self.path) as db:
                db.execute('INSERT INTO state VALUES(1,?,?) ON CONFLICT(id) DO UPDATE SET revision=excluded.revision,payload=excluded.payload',(current+1,encoded))
            self.cache=json.loads(encoded)
            return self.cache
