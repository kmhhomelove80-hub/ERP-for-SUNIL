from tempfile import TemporaryDirectory
from pathlib import Path
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from openpyxl import Workbook
from .importing import inspect_workbook, apply_import
from .models import Stock, Allocation, Item, ImportBatch

class ImportTests(TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name)/'fixture.xlsx'
        self.user = get_user_model().objects.create_user('importer',is_staff=True)
    def workbook(self, qty=8, duplicate=False, variant=False, expected=False):
        w = Workbook(); s = w.active; s.title = '재고_rawdata'
        s['B7'] = 'ITEM No.'
        loc = '11/3입고(담당자)' if expected else 'A01'
        for col,value in zip('BCDEF',['CODE1','모델','사양',10,loc]): s[f'{col}8'] = value
        s['G8'] = '=BROKEN(#REF!)'
        if duplicate or variant:
            for col,value in zip('BCDEF',['CODE1','별도모델','별도설명',2,loc if duplicate else 'B02']): s[f'{col}9'] = value
        p = w.create_sheet('Picking List');p['BA7']='ITEM No.'
        for col,value in zip(['BA','BB','BC','BD','BE','BF','BG'],['CODE1','모델','사양',qty,loc,'2026103001','호선1']): p[f'{col}8']=value
        w.save(self.path)
    def test_import_recomputes_reservations_and_preserves_seq(self):
        self.workbook()
        plan = inspect_workbook(self.path)
        self.assertFalse(Stock.objects.exists())
        apply_import(plan,self.user)
        self.assertEqual((Stock.objects.get().quantity,Stock.objects.get().reserved),(10,8))
        self.assertEqual(Allocation.objects.get().legacy_seq,'2026103001')
        with self.assertRaises(ValidationError): apply_import(plan,self.user)
        self.assertEqual(Stock.objects.count(),1)
    def test_overallocation_and_duplicate_buckets_are_rejected(self):
        self.workbook(qty=11)
        with self.assertRaises(ValidationError): inspect_workbook(self.path)
        self.workbook(duplicate=True)
        with self.assertRaises(ValidationError): inspect_workbook(self.path)
    def test_expected_requires_explicit_classification(self):
        self.workbook(expected=True)
        with self.assertRaises(ValidationError): inspect_workbook(self.path)
        plan = inspect_workbook(self.path,['11/3입고(담당자)'])
        self.assertEqual(plan['summary']['expected_quantity'],10)
        apply_import(plan,self.user)
        self.assertEqual(Stock.objects.get().status,'expected')
    def test_description_variants_are_preserved(self):
        self.workbook(variant=True)
        plan = inspect_workbook(self.path)
        self.assertEqual(len(plan['warnings']),1)
        apply_import(plan,self.user)
        self.assertEqual(Item.objects.get().name,'모델')
        self.assertEqual(Stock.objects.get(location='B02').legacy_name,'별도모델')
    def test_failed_import_rolls_back_all_records(self):
        self.workbook()
        with patch('inventory.importing.audit',side_effect=RuntimeError('audit unavailable')):
            with self.assertRaises(RuntimeError): apply_import(inspect_workbook(self.path),self.user)
        for model in [Item,Stock,Allocation,ImportBatch]: self.assertEqual(model.objects.count(),0)
