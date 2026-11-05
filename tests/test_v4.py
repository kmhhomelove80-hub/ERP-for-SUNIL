import io,tempfile,unittest,sqlite3,json
from pathlib import Path
from unittest.mock import patch
from app.raw_mapping import plan_raw,materialize
from app.service import initialize,replace_raw,rebuild
from app.store import Store
from app.shipments import Shipments
from app.closing import Closing,fingerprint
from test_workspace import fixture
from openpyxl import load_workbook

class RawAndManualTests(unittest.TestCase):
 def test_reordered_raw_headers(self):
  s=initialize(fixture());raw=[s['book']['HMD'][2][13:],s['book']['HMD'][3][13:]];raw=[list(reversed(r)) for r in raw]
  p=plan_raw(s,'HMD',raw);self.assertEqual(p['unresolved'],0)
  canonical,aliases=materialize(s,'HMD',raw,p,{})
  updated=replace_raw(s,'HMD',canonical)
  self.assertEqual(next(r['values'][0] for r in updated['orders'] if r['yard']=='HMD'),'8354EL7401')
 def test_duplicate_header_requires_manual_choice(self):
  s=initialize(fixture());raw=[s['book']['HMD'][2][13:],s['book']['HMD'][3][13:]];raw[0][1]=raw[0][44]
  p=plan_raw(s,'HMD',raw);self.assertGreater(p['unresolved'],0)
  with self.assertRaises(ValueError):materialize(s,'HMD',raw,p,{})
  materialize(s,'HMD',raw,p,{'45':45})
 def test_remember_renamed_header_after_manual_link(self):
  s=initialize(fixture());raw=[s['book']['HMD'][2][13:],s['book']['HMD'][3][13:]];raw[0][44]='새 수량 이름'
  p=plan_raw(s,'HMD',raw);canonical,aliases=materialize(s,'HMD',raw,p,{'45':45});s['raw_aliases']={'HMD':aliases}
  reverse=[list(reversed(r)) for r in raw];p=plan_raw(s,'HMD',reverse);self.assertEqual(p['unresolved'],0)
 def test_manual_quantity_and_delete_recalculate(self):
  s=initialize(fixture());first=s['orders'][0];s['manual_orders']={first['rowkey']:{'quantity':9}};s=rebuild(s)
  row=s['orders'][0];self.assertEqual(row['values'][4],9);self.assertEqual(row['values'][6],9)
  s['manual_orders'][row['rowkey']]={'deleted':True};s=rebuild(s);self.assertEqual(len(s['orders']),2)
 def test_raw_replacement_clears_only_its_manual_edits(self):
  s=initialize(fixture());s['manual_orders']={r['rowkey']:{'quantity':9} for r in s['orders']};s=rebuild(s)
  raw=[s['book']['HMD'][2][13:],s['book']['HMD'][3][13:]];updated=replace_raw(s,'HMD',raw)
  self.assertEqual(next(r['values'][4] for r in updated['orders'] if r['yard']=='HMD'),2)
  self.assertEqual(next(r['values'][4] for r in updated['orders'] if r['yard']=='HHI'),9)
 def test_state_restore(self):
  with tempfile.TemporaryDirectory() as d:
   store=Store(Path(d)/'db');s=store.commit(initialize(fixture()),0);new={**s,'manual_orders':{s['orders'][0]['rowkey']:{'deleted':True}}};s2=store.commit(rebuild(new),1)
   self.assertEqual(len(s2['orders']),2);restored=store.restore(1,2);self.assertEqual(len(restored['orders']),3);self.assertEqual(restored['revision'],3)

class LedgerClosingTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.path=Path(self.temp.name)/'db';self.ledger=Shipments(self.path)
  self.records=[{'values':['P',1,'B','품명',q,'CODE',3,None,'2026-01-01',1,'창고','2026-11-01',100,10,110],'unmatched':False} for q in [1,2]]
  self.ledger.confirm('key','sig','HHI',self.records);self.close=Closing(self.path);self.close.set_root(str(Path(self.temp.name)/'backup'))
 def test_quantity_delete_undo(self):
  ids=[r['id'] for r in self.ledger.rows()];self.ledger.edit([ids[0]],7)
  self.assertEqual(self.ledger.rows()[0]['values'][6],9)
  self.ledger.edit([ids[1]],delete=True);self.assertEqual(self.ledger.rows()[0]['values'][6],7)
  self.ledger.undo();self.assertEqual(len(self.ledger.rows()),2);self.ledger.undo();self.assertEqual(self.ledger.rows()[0]['values'][6],3)
 def test_weekly_close_exports_before_hiding(self):
  ids=[r['id'] for r in self.ledger.rows()];self.ledger.update([ids[0]],status='erp_completed');rows=self.ledger.rows(status='erp_completed')
  result=self.close.close('closekey','2026-W45',[ids[0]],fingerprint(rows),str(self.close.root()))
  self.assertEqual(len(self.ledger.rows()),1);p,data=self.close.file(result['id']);self.assertTrue(p.exists())
  w=load_workbook(io.BytesIO(data));self.assertEqual(w.active.max_row,2);self.assertEqual(w.active.cell(2,8).value,1)
  self.assertFalse(self.close.close('closekey','2026-W45',[ids[0]],'anything',str(self.close.root()))['inserted'])
 def test_stale_preview_refuses_close(self):
  ids=[r['id'] for r in self.ledger.rows()];self.ledger.update(ids,status='erp_completed');old=self.ledger.rows(status='erp_completed');self.ledger.edit([ids[0]],7)
  with self.assertRaises(ValueError):self.close.close('close','2026-W45',ids,fingerprint(old),str(self.close.root()))
  self.assertEqual(len(self.ledger.rows()),2);self.assertEqual(self.close.list(),[])
 def test_file_failure_leaves_all_rows(self):
  ids=[r['id'] for r in self.ledger.rows()];self.ledger.update(ids,status='erp_completed');rows=self.ledger.rows(status='erp_completed')
  with patch('app.closing.os.replace',side_effect=OSError('disk failure')):
   with self.assertRaises(OSError):self.close.close('close','2026-W45',ids,fingerprint(rows),str(self.close.root()))
  self.assertEqual(len(self.ledger.rows()),2);self.assertEqual(self.close.list(),[]);self.assertEqual(list(self.close.root().rglob('*.xlsx')),[])
 def test_weekly_filenames_do_not_overwrite(self):
  ids=[r['id'] for r in self.ledger.rows()];self.ledger.update([ids[0]],status='erp_completed');rows=self.ledger.rows(status='erp_completed');one=self.close.close('one','2026-W45',[ids[0]],fingerprint(rows),str(self.close.root()))
  self.ledger.update([ids[1]],status='erp_completed');rows=self.ledger.rows(status='erp_completed');two=self.close.close('two','2026-W45',[ids[1]],fingerprint(rows),str(self.close.root()));self.assertNotEqual(one['path'],two['path'])
 def test_legacy_schema_migrates(self):
  path=Path(self.temp.name)/'old'
  with sqlite3.connect(path) as db:
   db.execute('CREATE TABLE shipments(id INTEGER PRIMARY KEY,batch_id TEXT,yard TEXT,status TEXT,memo TEXT,payload TEXT)')
  Shipments(path)
  with sqlite3.connect(path) as db:self.assertTrue({'deleted','archived_id'}<={r[1] for r in db.execute('PRAGMA table_info(shipments)')})

class MissingCodeTotalsTests(unittest.TestCase):
 def test_different_unmatched_products_not_added_together(self):
  from app.engine import extract_selected
  s=initialize(fixture());first={'yard':'HHI','values':s['orders'][0]['values'][:]};second={'yard':'HHI','values':first['values'][:]}
  first['values'][5]=None;second['values'][5]=None;second['values'][0]='OTHER';first['values'][4]=1;second['values'][4]=2
  r=extract_selected([first,second],s['erp']);self.assertEqual(sorted(x['values'][6] for x in r['rows']),[1,2])
 def test_fractional_manual_quantity_is_retained(self):
  from app.order_edits import apply_manual
  s=initialize(fixture());row=s['orders'][0];updated=apply_manual([row],{row['rowkey']:{'quantity':0.5}})
  self.assertEqual(updated[0]['values'][6],0.5)
