import io
import uuid
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from openpyxl import Workbook
from .models import Item,Stock,ErpSnapshot,ReconciliationRow
from .reconciliation import inspect_erp_file,create_snapshot,update_review,latest_reference
from .picking import parse_paste,plan_picking

class ReconciliationTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_user('erp-manager',is_staff=True)
        self.item=Item.objects.create(code='ERP1')
        Stock.objects.create(item=self.item,location='A',quantity=10,reserved=3)
        Stock.objects.create(item=self.item,location='예정',status='expected',quantity=5)
    def plan(self):
        return dict(digest='a'*64,source_name='test.xlsx',rows=[dict(warehouse='Y09',code='ERP1',name='등기구',quantity=12,shipping_pending=20),dict(warehouse='Y02',code='ERP1',name='등기구',quantity=30,shipping_pending=4)])
    def snapshot(self,plan=None,token=None):
        return create_snapshot(request_id=token or uuid.uuid4(),label='회사 재고',erp_as_of=timezone.now(),plan=plan or self.plan(),actor=self.user)
    def file(self,rows):
        w=Workbook();s=w.active;s.title='ERP재고';s.append(['창고','ERP 코드','현재고','출하대기','모델명'])
        for row in rows:s.append(row)
        stream=io.BytesIO();w.save(stream)
        return SimpleUploadedFile('erp.xlsx',stream.getvalue())
    def test_snapshot_preserves_managed_qty_and_separates_pending(self):
        snapshot=self.snapshot();row=snapshot.rows.get(warehouse='Y09')
        self.assertEqual((row.received,row.expected,row.managed,row.reserved,row.erp_quantity,row.shipping_pending,row.difference),(10,5,15,3,12,20,3))
        self.assertEqual(Stock.objects.get(location='A').quantity,10)
        Stock.objects.filter(location='A').update(quantity=8)
        row.refresh_from_db();self.assertEqual(row.received,10)
        self.assertIsNone(snapshot.rows.get(warehouse='Y02').difference)
    def test_erp_missing_is_not_assumed_zero_and_union_contains_both_sides(self):
        plan=self.plan();plan['rows'][0]['code']='ERP-ONLY'
        snapshot=self.snapshot(plan)
        missing=snapshot.rows.get(warehouse='Y09',code='ERP1')
        self.assertIsNone(missing.erp_quantity);self.assertTrue(missing.needs_review)
        self.assertEqual(snapshot.rows.get(code='ERP-ONLY').difference,-12)
    def test_y02_only_upload_does_not_create_y09_comparison(self):
        plan=self.plan();plan['rows']=plan['rows'][1:]
        snapshot=self.snapshot(plan)
        self.assertEqual(snapshot.rows.count(),1)
        reference,latest=latest_reference('Y02',['ERP1'])
        self.assertEqual(reference['ERP1']['erp_quantity'],30)
    def test_shortage_shows_y02_reference_without_allocating_it(self):
        self.snapshot()
        preview=plan_picking(parse_paste('ERP1\t50'))
        self.assertEqual(preview['shortages'][0]['y02_quantity'],30)
        self.assertEqual(preview['shortages'][0]['shortage'],38)
        self.assertEqual(sum(r['quantity'] for r in preview['allocations']),12)
    def test_duplicate_confirmation_returns_same_snapshot(self):
        token=uuid.uuid4();at=timezone.now();plan=self.plan()
        data=dict(request_id=token,label='자료',erp_as_of=at,plan=plan,actor=self.user)
        first=create_snapshot(**data);second=create_snapshot(**data)
        self.assertEqual(first.pk,second.pk);self.assertEqual(ErpSnapshot.objects.count(),1)
    def test_invalid_file_duplicate_and_formula_are_rejected(self):
        for rows in [[['Y09','CODE',1,0,'등'],['Y09','CODE',2,0,'등']],[['Y09','CODE','=1+1',0,'등']],[['Y99','CODE',1,0,'등']]]:
            with self.subTest(rows=rows),self.assertRaises(ValidationError):inspect_erp_file(self.file(rows),'standard')
        with self.assertRaises(ValidationError):inspect_erp_file(SimpleUploadedFile('bad.xlsx',b'invalid'),'standard')
    def test_review_requires_reason_and_action_and_preserves_quantity(self):
        row=self.snapshot().rows.get(warehouse='Y09')
        with self.assertRaises(ValidationError):update_review(row_id=row.pk,state='resolved',reason='원인',action_note='',actor=self.user)
        update_review(row_id=row.pk,state='resolved',reason='반영 시점 차이',action_note='회사 ERP 수정 확인',actor=self.user)
        row.refresh_from_db();self.assertEqual((row.difference,row.review_state),(3,'resolved'))
    def test_snapshot_logging_failure_rolls_back(self):
        with patch('inventory.reconciliation.audit',side_effect=RuntimeError('audit unavailable')):
            with self.assertRaises(RuntimeError):self.snapshot()
        self.assertFalse(ErpSnapshot.objects.exists());self.assertFalse(ReconciliationRow.objects.exists())
    def test_web_upload_preview_confirm_and_filters(self):
        self.client.force_login(self.user)
        response=self.client.post(reverse('erp-upload'),dict(action='preview',request_id=uuid.uuid4(),label='브라우저 자료',erp_as_of='2026-10-01T10:00',layout='standard',file=self.file([['Y09','ERP1',12,20,'등'],['Y02','ERP1',30,4,'등']])))
        self.assertContains(response,'창고별 자료 확인');self.assertFalse(ErpSnapshot.objects.exists())
        token=response.context['confirm_form']['preview_token'].value()
        response=self.client.post(reverse('erp-upload'),dict(action='confirm',preview_token=token,confirmed='on'))
        self.assertEqual(response.status_code,302)
        self.assertContains(self.client.get(response.url),'ERP1')
        response=self.client.get(response.url,dict(warehouse='Y02',issues=0))
        self.assertContains(response,'본사 참고 자료')
    def test_standard_template_download_and_nonstaff_restriction(self):
        self.client.force_login(self.user)
        response=self.client.get(reverse('erp-template'));self.assertEqual(response.status_code,200)
        self.assertTrue(response.content.startswith(b'PK'))
        viewer=get_user_model().objects.create_user('viewer');self.client.force_login(viewer)
        self.assertEqual(self.client.get(reverse('erp-list')).status_code,302)

    def test_signed_company_erp_values_are_preserved_without_negative_physical_stock(self):
        plan=inspect_erp_file(self.file([['Y09','ERP1',-2,-3,'원본 음수']]),'standard')
        snapshot=self.snapshot(plan)
        row=snapshot.rows.get()
        self.assertEqual((row.erp_quantity,row.shipping_pending,row.difference),(-2,-3,17))
        self.assertEqual(Stock.objects.get(location='A').quantity,10)
