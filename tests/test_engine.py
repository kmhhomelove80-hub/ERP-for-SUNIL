import unittest
from app.engine import *
class EngineTests(unittest.TestCase):
    def erp(self):return [['현대중공업 M','대형','8354','8354EL7401','',4,10,0,'8354EL7401','CODE','2025-05-29',540,2,100,10,110,'2026-01-01','품명']]
    def book(self,ap=None,as_=None,bj=None):
        r=[None]*62
        for c,v in {14:'835406',16:'8354EL74',17:'01',18:'A500',20:'등기구',25:'2023-11-14',42:ap,45:as_,58:2,61:'기존 납품처',62:bj}.items():r[c-1]=v
        return {'HMD':[[],[],[],r], 'SET':[]}
    def test_hmd_original(self):
        r,_=build_source(self.book(),self.erp(),'HMD');self.assertEqual(r[0][10:12],['기존 납품처','2023-11-14'])
    def test_hmd_recipient(self):
        r,_=build_source(self.book('2026-02-01','수령자','입고장소'),self.erp(),'HMD');self.assertEqual(r[0][10:12],['수령자','2026-02-01'])
    def test_hmd_fallback(self):
        r,_=build_source(self.book('2026-02-01','  ','입고장소'),self.erp(),'HMD');self.assertEqual(r[0][10:12],['입고장소','2026-02-01'])
    def test_empty_destination_removed(self):
        r=extract(self.book('2026-02-01'),self.erp(),'HMD',[]);self.assertEqual(r['stats']['removed'],1);self.assertEqual(r['rows'],[])
    def test_consolidation_quantity_only(self):
        b=self.book();b['HMD'].append(b['HMD'][3][:]);r=extract(b,self.erp(),'HMD',[])
        self.assertEqual(r['stats']['final'],1);self.assertEqual(r['rows'][0]['values'][4],4);self.assertEqual(r['rows'][0]['values'][12:],[100,10,110])
    def test_exact_filter(self):self.assertEqual(extract(self.book(),self.erp(),'HMD',[{'por':'other','block':''}])['rows'],[])
    def test_first_erp_match(self):
        e=self.erp();second=e[0][:];second[9]='OTHER';e.append(second)
        r,_=build_source(self.book(),e,'HMD');self.assertEqual(r[0][5],'CODE')
    def test_hshi_por_and_block(self):
        row=[None]*41
        for c,v in {14:8324,15:'BLOCK',17:'8324NLE ',18:'01',20:'등',29:'2026-01-01',41:1}.items():row[c-1]=v
        b={'HSHI':[[],row]};r=extract(b,self.erp(),'HSHI',[{'por':'8324NLE01','block':'BLOCK'}]);self.assertEqual(r['stats']['final'],1)
    def test_small(self):
        e=self.erp();e[0][1]='중소';r=extract({},e,'중소',[]);self.assertEqual(r['rows'][0]['values'][12:],[100,10,110])
if __name__=='__main__':unittest.main()

class ERPTests(unittest.TestCase):
    def test_samho_por_and_changed_due(self):
        row=[None]*36
        for c,v in {1:'삼호거래처',3:'CODE',4:'품명',8:'NLE-01',10:8324,11:'NLE-01',12:'2026-05-27',16:'2026-07-10',20:738,22:1}.items():row[c-1]=v
        headers=[None]*36;headers[2]='품목코드';headers[9]='현장명'
        b={'REF':[['거래처','분류','구분'],['삼호거래처','현대삼호중공업','대형']]}
        e=convert_erp(b,[headers,row]);self.assertEqual(e[0][8],'8324NLE01');self.assertEqual(e[0][16],'2026-07-10')
    def test_invalid_number(self):
        with self.assertRaises(ValueError):number('수량 미정')

class ClassificationTests(unittest.TestCase):
    def test_saved_mapping_survives_reopen(self):
        import tempfile
        from pathlib import Path
        from app.classifications import ClassificationStore
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'test.sqlite3';store=ClassificationStore(path)
            self.assertEqual(store.all()['[선박]진우엔지니어링주식회사'][1],'중소')
            store.save([('새 거래처','새 조선소','대형')])
            self.assertEqual(ClassificationStore(path).all()['새 거래처'],('새 조선소','대형'))
    def test_override_takes_precedence(self):
        header=[None]*36;header[2]='품목코드';header[9]='현장명'
        row=[None]*36
        for c,v in {1:'거래처',3:'CODE',8:'사양',10:'호선'}.items():row[c-1]=v
        book={'REF':[['거래처','분류','구분'],['거래처','기존','대형']]}
        result=convert_erp(book,[header,row],{'거래처':('수정','중소')})
        self.assertEqual(result[0][:2],['수정','중소'])
