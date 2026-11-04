import io,tempfile,unittest
from pathlib import Path
from openpyxl import Workbook
from app.yards import YardConfig,configured_source
from app.engine import extract_selected,HEADERS
from app.service import initialize,rebuild,replace_raw
from app.advanced import parse_shipment_file
from app.shipments import Shipments
from test_workspace import fixture

CFG={'name':'SHI','header_row':1,'match_cols':[1,2],'erp_match_cols':[9],'qty':5,'hull':1,'block':3,'title':4,'due':6,'destination':7,'remove_hyphens':True,'remove_spaces':False}
RAW=[['호선','사양','블록','품명','수량','납기','납품처'],['8354','EL74-01','A500','새 야드 등',3,'2026-07-01','야드 창고']]

class AdvancedTests(unittest.TestCase):
    def state(self):
        s=initialize(fixture());s['book']['SHI']=RAW;s['yards']={'SHI':CFG};return rebuild(s)
    def test_new_yard_matches_erp_and_survives_rebuild(self):
        s=self.state();r=next(r for r in s['orders'] if r['yard']=='SHI')['values'];self.assertEqual(r[0],'8354EL7401');self.assertEqual(r[5],'CODE');self.assertEqual(r[4],3)
    def test_custom_raw_replace_preserves_other_yards(self):
        s=self.state();raw=[RAW[0],RAW[1][:]];raw[1][4]=9
        updated=replace_raw(s,'SHI',raw);self.assertEqual(updated['book']['HHI'],s['book']['HHI']);self.assertEqual(next(r for r in updated['orders'] if r['yard']=='SHI')['values'][4],9)
    def test_bad_custom_columns_rejected(self):
        with self.assertRaises(ValueError):replace_raw(self.state(),'SHI',[['wrong','headers']])
    def test_reserved_yard_rejected(self):
        with self.assertRaises(ValueError):YardConfig(**{**CFG,'name':'HMD'}).checked()
    def test_invalid_columns_rejected(self):
        with self.assertRaises(ValueError):YardConfig(**{**CFG,'qty':0}).checked()
    def test_selected_rows_not_por_expansion(self):
        s=initialize(fixture());selection=[s['orders'][0]];r=extract_selected(selection,s['erp'])
        self.assertEqual(r['stats']['selected'],1);self.assertEqual(r['stats']['quantity'],3)
    def test_mixed_yards_never_merge(self):
        s=initialize(fixture());r=extract_selected(s['orders'],s['erp'])
        self.assertEqual(r['stats']['final'],3);self.assertEqual({v['yard'] for v in r['rows']},{'HMD','HHI','HSHI'})
        for row in r['rows']:self.assertEqual(row['values'][4],row['values'][6])
    def test_partial_same_por_only_selected_qty(self):
        s=initialize(fixture());one=s['orders'][0];another={'yard':one['yard'],'values':one['values'][:]};another['values'][4]=99
        r=extract_selected([one],s['erp']);self.assertEqual(r['stats']['quantity'],3)
    def test_missing_destination_rule_applies_only_hmd(self):
        s=initialize(fixture());rows=[]
        for r in s['orders']:
            r={'yard':r['yard'],'values':r['values'][:]};r['values'][10]='';rows.append(r)
        result=extract_selected(rows,s['erp']);self.assertEqual(result['stats']['removed'],1);self.assertEqual(result['stats']['final'],2)
    def workbook(self):
        from openpyxl.styles import PatternFill
        w=Workbook();s=w.active;s.append(HEADERS+['공급가액','부가세','합계금액'])
        for i,status in enumerate(['FF0000','FFFF00',None],2):
            s.append(['POR',8354,'B','품명',1,'CODE',3,None,'2026-01-01',540,'창고','2026-07-01',100,10,110])
            if status:
                for c in s[i]:c.fill=PatternFill('solid',fgColor=status)
        s.merge_cells('G2:G4');out=io.BytesIO();w.save(out);return out.getvalue()
    def test_import_colors_and_merged_totals(self):
        records,_=parse_shipment_file(self.workbook(),'기존자료')
        self.assertEqual([r['import_status'] for r in records],['shortage','erp_completed','pending']);self.assertTrue(all(r['values'][6]==3 for r in records))
    def test_import_idempotency_and_mixed_yard_confirm(self):
        records,_=parse_shipment_file(self.workbook(),'기존자료')
        with tempfile.TemporaryDirectory() as d:
            ledger=Shipments(Path(d)/'ledger.db');ledger.import_records('file','signature','기존자료',records)
            self.assertFalse(ledger.import_records('file','signature','기존자료',records)['inserted'])
            self.assertEqual(len(ledger.rows()),3)
            self.assertEqual(len(ledger.rows(status='erp_completed')),1)
    def test_import_wrong_headers_rejected(self):
        w=Workbook();w.active.append(['wrong']);b=io.BytesIO();w.save(b)
        with self.assertRaises(ValueError):parse_shipment_file(b.getvalue(),'기존자료')
