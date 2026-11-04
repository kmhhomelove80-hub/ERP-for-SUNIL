"""사용자가 지정한 거래처 분류를 로컬 SQLite에 보관합니다."""
import sqlite3
from pathlib import Path

DEFAULTS = {
    '[선박]진우엔지니어링주식회사': ('[선박]진우엔지니어링주식회사', '중소'),
    '[선박]세진기술산업(주)': ('[선박]세진기술산업(주)', '중소'),
}

class ClassificationStore:
    def __init__(self, path=None):
        self.path=Path(path) if path else Path(__file__).parent.parent/'data/classifications.sqlite3'
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS classifications (customer TEXT PRIMARY KEY, name TEXT NOT NULL, category TEXT NOT NULL)')
            db.executemany('INSERT OR IGNORE INTO classifications VALUES (?,?,?)',[(k,*v) for k,v in DEFAULTS.items()])
    def connect(self):return sqlite3.connect(self.path)
    def all(self):
        with self.connect() as db:return {k:(n,c) for k,n,c in db.execute('SELECT customer,name,category FROM classifications ORDER BY customer')}
    def save(self, rows):
        with self.connect() as db:
            db.executemany('INSERT INTO classifications VALUES (?,?,?) ON CONFLICT(customer) DO UPDATE SET name=excluded.name, category=excluded.category',rows)
