import tempfile
import unittest
from pathlib import Path
from copy import deepcopy
from app.store import Store
from app.service import initialize, replace_raw, rebuild, filter_orders

def fixture():
    headers=[None]*36;headers[0]='거 래 처';headers[2]='품목코드';headers[9]='현장명'
    r=[None]*36
    for c,v in {1:'거래처',3:'CODE',4:'품명',8:'8354-EL74-01',10:'H8354',12:'2025-05-29',16:'2026-07-10',20:540,22:2,28:100,29:10,30:110}.items():r[c-1]=v
    book={'REF':[['거래처','조선소','구분'],['거래처','현대중공업 M','대형']],'SET':[['현장명','주문사양','POR','품목','잔량','SET'],[None,None,'8354EL7401',None,None,'SET(2)']],'ERP':[[None]*19+headers,[None]*19+r]}
    for name,header in [('HMD',3),('HHI',3),('HSHI',1)]:
        h=['열'+str(i) for i in range(1,76)];row=[None]*75
        if name=='HMD':fields={14:'835406',16:'8354EL74',17:'01',18:'A500',20:'품명',25:'2026-01-01',58:2,61:'창고'}
        elif name=='HHI':fields={16:'8354EL7401',19:8354,20:'A500',24:'품명',26:3,30:'2026-01-01',32:'창고'}
        else:fields={14:8354,15:'A500',17:'8354EL74',18:'01',20:'품명',29:'2026-01-01',36:'창고',41:4}
        for c,v in fields.items():row[c-1]=v
        book[name]=[[] for _ in range(header-1)]+[h,row]
    return book

class WorkspaceTests(unittest.TestCase):
    def test_persistent_snapshot_and_revision(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'store.db';s=Store(p);s.commit(initialize(fixture()),0)
            loaded=Store(p).read();self.assertEqual(len(loaded['orders']),3)
            with self.assertRaises(ValueError):s.commit(loaded,0)
    def test_yard_replacement_not_append(self):
        state=initialize(fixture());raw=[state['book']['HMD'][2][13:],state['book']['HMD'][3][13:]]
        raw[1][58-14]=12
        updated=replace_raw(state,'HMD',raw)
        self.assertEqual(next(x['values'][4] for x in updated['orders'] if x['yard']=='HMD'),12)
        self.assertEqual(updated['book']['HHI'],state['book']['HHI'])
        self.assertEqual(len(replace_raw(updated,'HMD',raw)['orders']),3)
    def test_erp_refresh_all_yards(self):
        state=initialize(fixture());raw=[r[19:] for r in state['book']['ERP']];raw[1][2]='NEWCODE'
        updated=replace_raw(state,'ERP',raw)
        self.assertTrue(all(r['values'][5]=='NEWCODE' for r in updated['orders']))
    def test_empty_yard_snapshot_clears_only_yard(self):
        state=initialize(fixture());updated=replace_raw(state,'HMD',[state['book']['HMD'][2][13:]])
        self.assertEqual({r['yard'] for r in updated['orders']},{'HHI','HSHI'})
    def test_invalid_raw_keeps_previous(self):
        state=initialize(fixture());saved=deepcopy(state)
        with self.assertRaises(ValueError):replace_raw(state,'HMD',[['wrong']])
        self.assertEqual(state,saved)
    def test_ref_set_recalculation(self):
        state=initialize(fixture());state['book']['SET'][1][5]='새 SET';updated=rebuild(state)
        self.assertTrue(all(r['values'][7]=='새 SET' for r in updated['orders']))
    def test_search_filters(self):
        state=initialize(fixture());rows=list(filter_orders(state,yard='HMD',code='CODE',date_from='2026-01-01'))
        self.assertEqual(len(rows),1)
        self.assertEqual(list(filter_orders(state,q='없는자료')),[])
    def test_unknown_customer_is_listed(self):
        b=fixture();b['ERP'][1][19]='신규거래처';state=initialize(b)
        self.assertEqual(state['unclassified'],['신규거래처'])
