import tempfile
import unittest
from pathlib import Path
from app.shipments import Shipments
from app.store import Store
from app.service import initialize
from test_workspace import fixture

class ShipmentTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'test.sqlite3';self.store=Store(self.path);self.ledger=Shipments(self.path)
        self.records=[{'values':['POR','8354','BLOCK','품명',2,'CODE',2,None,'2026-01-01',540,'납품처','2026-07-10',100,10,110],'unmatched':False}]
    def test_confirm_pending_and_idempotent(self):
        a=self.ledger.confirm('request-1','signature','HMD',self.records)
        b=self.ledger.confirm('request-1','signature','HMD',self.records)
        self.assertTrue(a['inserted']);self.assertFalse(b['inserted']);self.assertEqual(len(self.ledger.rows()),1)
        self.assertEqual(self.ledger.rows()[0]['status'],'pending')
    def test_distinct_batch_accumulates(self):
        self.ledger.confirm('request-1','sig','HMD',self.records);self.ledger.confirm('request-2','sig','HMD',self.records)
        self.assertEqual(len(self.ledger.rows()),2)
    def test_status_memo_and_restart(self):
        self.ledger.confirm('r','s','HMD',self.records);id=self.ledger.rows()[0]['id']
        self.ledger.update([id],status='shortage',memo='재고 부족')
        self.assertEqual(Shipments(self.path).rows(status='shortage')[0]['memo'],'재고 부족')
        self.ledger.update([id],status='erp_completed');self.assertEqual(len(self.ledger.rows(status='erp_completed')),1)
    def test_snapshot_survives_raw_initialization(self):
        self.ledger.confirm('r','s','HMD',self.records);self.records[0]['values'][4]=999
        self.store.commit(initialize(fixture()),0)
        self.assertEqual(self.ledger.rows()[0]['values'][4],2)
    def test_invalid_status_atomic(self):
        self.ledger.confirm('r','s','HMD',self.records);id=self.ledger.rows()[0]['id']
        with self.assertRaises(ValueError):self.ledger.update([id],status='invalid')
        self.assertEqual(self.ledger.rows()[0]['status'],'pending')
    def test_missing_id_does_not_modify_other_rows(self):
        self.ledger.confirm('r','s','HMD',self.records);id=self.ledger.rows()[0]['id']
        with self.assertRaises(ValueError):self.ledger.update([id,99999],status='shortage')
        self.assertEqual(self.ledger.rows()[0]['status'],'pending')
    def test_same_request_different_payload_rejected(self):
        self.ledger.confirm('r','s','HMD',self.records)
        with self.assertRaises(ValueError):self.ledger.confirm('r','other','HMD',self.records)
    def test_filters(self):
        self.ledger.confirm('r','s','HMD',self.records)
        self.assertEqual(len(self.ledger.rows(q='CODE',yard='HMD')),1)
        self.assertEqual(len(self.ledger.rows(yard='HHI')),0)
