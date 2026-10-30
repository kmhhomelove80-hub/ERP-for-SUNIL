import io,uuid
from openpyxl import load_workbook
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from .models import Item,Stock
from .reconciliation import create_snapshot

class ExcelExportTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_user('exporter',is_staff=True)
        self.item=Item.objects.create(code='EXPORT1',name='=HYPERLINK("https://example.invalid")',specification='원본 설명')
        Stock.objects.create(item=self.item,location='A01',quantity=10)
        other=Item.objects.create(code='OTHER');Stock.objects.create(item=other,location='B02',quantity=3)
        self.client.force_login(self.user)
    def test_filtered_stock_excel_preserves_text_without_formula(self):
        response=self.client.get('/stock/export/',{'q':'EXPORT1'})
        self.assertEqual(response.status_code,200)
        book=load_workbook(io.BytesIO(response.content),data_only=False)
        self.assertEqual(book.active.max_row,2)
        self.assertEqual(book.active['B2'].value,self.item.name)
        self.assertEqual(book.active['B2'].data_type,'s')
        self.assertEqual(book.active['G2'].value,10)
        book.close()
    def test_erp_export_preserves_missing_signed_values_and_review_text(self):
        plan=dict(digest='a'*64,source_name='source.xlsx',rows=[dict(warehouse='Y09',code='EXPORT1',name='품목',quantity=-1,shipping_pending=-2)])
        snapshot=create_snapshot(request_id=uuid.uuid4(),label='비교',erp_as_of=timezone.now(),plan=plan,actor=self.user)
        response=self.client.get(f'/erp/{snapshot.pk}/export/',{'issues':'1'})
        book=load_workbook(io.BytesIO(response.content));sheet=book.active
        exported={row[1]:row for row in sheet.iter_rows(min_row=2,values_only=True)}
        self.assertEqual(exported['EXPORT1'][3],-1)
        self.assertEqual(exported['EXPORT1'][7],11)
        self.assertIsNone(exported['OTHER'][3])
        book.close()
    def test_viewer_cannot_export_bulk_inventory(self):
        viewer=get_user_model().objects.create_user('mobile')
        self.client.force_login(viewer)
        self.assertEqual(self.client.get('/stock/export/').status_code,302)

    def test_oversized_export_returns_clear_error_instead_of_partial_file(self):
        from unittest.mock import patch
        with patch('inventory.exports.MAX_EXPORT_ROWS',1):
            response=self.client.get('/stock/export/')
        self.assertEqual(response.status_code,400)
        self.assertContains(response,'검색 범위를 좁혀주세요',status_code=400)
