import io
import uuid
from datetime import timedelta
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.core import signing
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, SimpleTestCase
from django.utils import timezone
from openpyxl import Workbook
from .bulk_receipts import parse_receipts, receive_batch
from .customer_picking import parse_customer, customer_print_groups
from .models import Item, Stock, Receipt, Allocation, AuditEvent, PickingOrder, ErpSnapshot
from .picking import parse_paste, plan_picking, confirm_picking
from .reconciliation import inspect_erp_exports
from .services import reserve, cancel_selected


def xlsx(rows):
    w=Workbook();s=w.active
    for row in rows:s.append(row)
    stream=io.BytesIO();w.save(stream)
    return SimpleUploadedFile('rows.xlsx',stream.getvalue())


def erp_file(code='CODE', quantity='1.0', pending='.0'):
    text='ABC분류\t품목코드\t품목명\t현재고\t출하대기\n\t'+code+'\t시험 조명\t'+quantity+'\t'+pending+'\n합계\t\t\t'+quantity+'\t'+pending+'\n'
    return SimpleUploadedFile('y09.xls',text.encode('cp949'))


class DataInputTests(SimpleTestCase):
    def test_picking_quantity_first_and_reversed_order_error(self):
        from .picking import parse_quantity_code
        self.assertEqual(parse_quantity_code('4\tCODE1\n8\tCODE1'),[{'code':'CODE1','quantity':12}])
        with self.assertRaisesMessage(ValidationError,'첫 열은'):
            parse_quantity_code('CODE1\t4')
        # Numeric ERP codes stay strings and preserve leading zeros.
        self.assertEqual(parse_quantity_code('3\t000123'),[{'code':'000123','quantity':3}])

    def test_receipt_header_title_and_excel_input(self):
        rows=parse_receipts(upload=xlsx([['2026.10.31 입고 LIST'],['품목코드','품목명','사양','수량','Location'],['NEW','품명','사양',4,'A'],['NEW','품명','사양',2,'B']]))
        self.assertEqual([r['quantity'] for r in rows],[4,2])
        self.assertEqual(rows[1]['location'],'B')

    def test_receipt_bad_qty_or_missing_location_rejected(self):
        for text in ['X\t명\t사양\t1.5\tA','X\t명\t사양\t-1\tA','X\t명\t사양\t1\t','X\t명\t사양\t=1+2\tA','X\t명\t사양\t1\tA\textra']:
            with self.subTest(text=text),self.assertRaises(ValidationError):parse_receipts(pasted=text)

    def test_erp_cp949_signed_values_and_summary_exclusion(self):
        result=inspect_erp_exports({'Y09':erp_file(quantity='-1,234.0',pending='.0'),'Y02':erp_file(quantity='2.0',pending='-3.0')})
        self.assertEqual(len(result['rows']),2)
        self.assertEqual([(r['warehouse'],r['quantity'],r['shipping_pending']) for r in result['rows']],[('Y09',-1234,0),('Y02',2,-3)])

    def test_erp_rejects_missing_headers_fraction_formula_duplicate(self):
        for f in [SimpleUploadedFile('bad.xls',b'code\tqty\nX\t1'),erp_file(quantity='1.2'),erp_file(quantity='=1+2')]:
            with self.assertRaises(ValidationError):inspect_erp_exports({'Y09':f})
        f=erp_file();content=f.read().replace(b'\n',b'\n',1)
        content+=content.splitlines()[1]+b'\n'
        with self.assertRaises(ValidationError):inspect_erp_exports({'Y09':SimpleUploadedFile('dup.xls',content)})

    def test_customer_six_columns_and_combined_sheet(self):
        rows=parse_customer(pasted='P\t4282\tT51C\t고객 조명\t4\tC\nP\t4282\tT62C\t고객 조명\t8\tC')
        self.assertEqual([r['quantity'] for r in rows],[4,8])
        rows=parse_customer(upload=xlsx([['ITEM','모델',None,None,None,None,None,'POR번호','호선','블록','품명','수량','ERP CODE','합계','Location'],[None]*7+['P','4282','B','등',3,'C',3,'ignore']]))
        self.assertEqual(rows[0]['code'],'C')


class ImprovementWorkflowTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_user('manager',is_staff=True)
        self.mobile=get_user_model().objects.create_user('mobile')
        self.item=Item.objects.create(code='C',name='기존 모델',specification='기존 사양')
        self.stock=Stock.objects.create(item=self.item,location='A',quantity=100)
        self.client.force_login(self.user)

    def test_batch_atomic_idempotent_existing_master_preserved(self):
        rows=parse_receipts(pasted='C\t새 설명\t새 사양\t3\tA\nNEW\t신규\t사양\t5\tB')
        request_id=uuid.uuid4()
        receive_batch(request_id=request_id,rows=rows,actor=self.user)
        receive_batch(request_id=request_id,rows=rows,actor=self.user)
        self.stock.refresh_from_db();self.item.refresh_from_db()
        self.assertEqual(self.stock.quantity,103)
        self.assertEqual(self.item.name,'기존 모델')
        self.assertEqual(Receipt.objects.count(),2)
        with self.assertRaises(ValidationError): receive_batch(request_id=request_id,rows=rows,actor=self.mobile)

    def test_batch_overflow_rolls_back_new_items_and_first_receipt(self):
        self.stock.quantity=2147483647;self.stock.save()
        rows=parse_receipts(pasted='NEW\t신규\t사양\t1\tB\nC\t모델\t사양\t1\tA')
        with self.assertRaises(ValidationError):receive_batch(request_id=uuid.uuid4(),rows=rows,actor=self.user)
        self.assertFalse(Item.objects.filter(code='NEW').exists());self.assertFalse(Receipt.objects.exists())

    def test_batch_audit_failure_rolls_back_all_rows(self):
        rows=parse_receipts(pasted='C\t모델\t사양\t2\tA')
        with patch('inventory.bulk_receipts.audit',side_effect=RuntimeError('audit failure')):
            with self.assertRaises(RuntimeError):receive_batch(request_id=uuid.uuid4(),rows=rows,actor=self.user)
        self.stock.refresh_from_db();self.assertEqual(self.stock.quantity,100)
        self.assertFalse(Receipt.objects.exists())

    def test_bulk_web_preview_confirm_replay_and_tamper(self):
        response=self.client.post('/receipts/bulk/',dict(action='preview',request_id=uuid.uuid4(),pasted_rows='C\t새 설명\t사양\t2\tA'))
        self.assertContains(response,'기존 품목 설명과 다른 코드')
        token=response.context['confirm_form']['preview_token'].value()
        self.assertEqual(Receipt.objects.count(),0)
        self.assertEqual(self.client.post('/receipts/bulk/',dict(action='confirm',preview_token=token,confirmed='on')).status_code,302)
        self.client.post('/receipts/bulk/',dict(action='confirm',preview_token=token,confirmed='on'))
        self.assertEqual(Receipt.objects.count(),1)
        self.assertContains(self.client.post('/receipts/bulk/',dict(action='confirm',preview_token=token+'bad',confirmed='on')),'유효하지 않습니다')

    def customer_order(self, quantity=12):
        customers=parse_customer(pasted=f'POR\t4282\tB1\t고객 조명\t4\tC\nPOR\t4282\tB2\t고객 조명\t{quantity-4}\tC')
        rows=parse_paste(f'C\t{quantity}')
        return confirm_picking(request_id=uuid.uuid4(),project='고객호선',rows=rows,preview=plan_picking(rows),allow_shortage=True,actor=self.user,customer_rows=customers)

    def test_customer_print_split_locations_shortage_and_no_double_reservation(self):
        self.stock.quantity=7;self.stock.save()
        Stock.objects.create(item=self.item,location='B',quantity=3)
        order=self.customer_order()
        groups=customer_print_groups(order)
        self.assertEqual(groups[0]['total'],12)
        self.assertEqual(groups[0]['shortage'],2)
        self.assertEqual(groups[0]['locations'],[{'location':'A','quantity':7},{'location':'B','quantity':3}])
        before=Allocation.objects.count()
        response=self.client.get(f'/picking/{order.pk}/customer/')
        self.assertContains(response,'부족 2ea');self.assertContains(response,'rowspan="2"')
        self.assertEqual(Allocation.objects.count(),before)
        cancel_selected(allocation_ids=list(order.allocations.values_list('pk',flat=True)),actor=self.user)
        self.assertEqual(customer_print_groups(order)[0]['shortage'],12)

    def test_customer_web_input_reaches_order_and_print(self):
        response=self.client.post('/picking/new/',dict(action='preview',request_id=uuid.uuid4(),project='고객',customer_rows='P\t42\tB\t고객등\t3\tC'))
        token=response.context['confirm_form']['preview_token'].value()
        response=self.client.post('/picking/new/',dict(action='confirm',preview_token=token))
        self.assertEqual(response.status_code,302)
        order=PickingOrder.objects.get()
        self.assertEqual(order.payload['customer_rows'][0]['por'],'P')
        self.assertContains(self.client.get(response.url),'고객 양식 인쇄')

    def test_stale_customer_preview_keeps_original_six_columns(self):
        response=self.client.post('/picking/new/',dict(action='preview',request_id=uuid.uuid4(),project='고객',customer_rows='POR-ORIGINAL\t42\tB\t고객등\t3\tC'))
        token=response.context['confirm_form']['preview_token'].value()
        reserve(stock_id=self.stock.pk,project='변경',quantity=99,actor=self.user)
        response=self.client.post('/picking/new/',dict(action='confirm',preview_token=token))
        self.assertContains(response,'POR-ORIGINAL')
        self.assertFalse(PickingOrder.objects.exists())

    def test_erp_export_web_both_uploads_and_replay_preserves_stock(self):
        response=self.client.post('/erp/upload/',dict(action='preview',request_id=uuid.uuid4(),label='직접 추출',erp_as_of='2026-10-01T10:00',layout='export',y09_file=erp_file('C','99.0','10.0'),y02_file=erp_file('C','50.0','-1.0')))
        self.assertContains(response,'Y09');self.assertContains(response,'Y02')
        token=response.context['confirm_form']['preview_token'].value()
        for _ in range(2):self.client.post('/erp/upload/',dict(action='confirm',preview_token=token,confirmed='on'))
        self.assertEqual(ErpSnapshot.objects.count(),1)
        self.assertEqual(ErpSnapshot.objects.get().rows.get(warehouse='Y09').difference,1)
        self.stock.refresh_from_db();self.assertEqual(self.stock.quantity,100)

    def test_filter_all_covers_pages_only_target_project_and_seq(self):
        # > one page, each one-unit reservation on a 100-unit bucket.
        for i in range(52):
            a=reserve(stock_id=self.stock.pk,project='목표',quantity=1,actor=self.user)
            a.legacy_seq='SEQ-01';a.save()
        other=reserve(stock_id=self.stock.pk,project='기타',quantity=1,actor=self.user)
        response=self.client.get('/allocations/?q=목표&seq=SEQ-01')
        self.assertEqual(len(response.context['allocations']),50)
        self.assertEqual(response.context['total'],52)
        token=response.context['filter_token']
        response=self.client.post('/allocations/?q=목표&seq=SEQ-01',dict(scope='filtered',filter_token=token,action='release',confirmed='on',shipped_at=(timezone.now()-timedelta(minutes=1)).isoformat()))
        self.assertEqual(response.status_code,302)
        self.assertEqual(Allocation.objects.filter(state='released').count(),52)
        other.refresh_from_db();self.assertEqual(other.state,'reserved')

    def test_filter_stale_tamper_outside_selection_are_rejected(self):
        first=reserve(stock_id=self.stock.pk,project='목표',quantity=1,actor=self.user)
        other=reserve(stock_id=self.stock.pk,project='다른',quantity=1,actor=self.user)
        token=self.client.get('/allocations/?q=목표').context['filter_token']
        reserve(stock_id=self.stock.pk,project='목표',quantity=1,actor=self.user)
        data=dict(scope='filtered',filter_token=token,action='cancel',confirmed='on')
        self.assertContains(self.client.post('/allocations/?q=목표',data),'필터 결과가 변경')
        data['filter_token']=token+'bad'
        self.assertContains(self.client.post('/allocations/?q=목표',data),'필터 미리보기가 만료')
        self.client.post('/allocations/?q=목표',dict(allocations=[other.pk],scope='selected',action='cancel',confirmed='on'))
        other.refresh_from_db();self.assertEqual(other.state,'reserved')
        first.refresh_from_db();self.assertEqual(first.state,'reserved')

    def test_partial_search_returns_results_without_replacing_input(self):
        response=self.client.get('/?partial=1&q=기존 사양')
        self.assertContains(response,'현재고 합계')
        self.assertNotContains(response,'<html')
        self.assertNotContains(response,'id="stock-filter"')
        self.assertContains(self.client.get('/?partial=1&q=nomatch'),'조회할 재고가 없습니다')


    def test_customer_repeated_nonadjacent_code_does_not_reuse_allocation(self):
        other=Item.objects.create(code='D');Stock.objects.create(item=other,location='D1',quantity=3)
        customer=parse_customer(pasted='P\t42\tB1\t등\t2\tC\nP\t42\tB2\t부품\t3\tD\nP\t42\tB3\t등\t4\tC')
        rows=parse_paste('C\t6\nD\t3')
        order=confirm_picking(request_id=uuid.uuid4(),project='반복코드',rows=rows,preview=plan_picking(rows),allow_shortage=False,actor=self.user,customer_rows=customer)
        groups=customer_print_groups(order)
        self.assertEqual([g['total'] for g in groups],[2,3,4])
        self.assertEqual([g['locations'][0]['quantity'] for g in groups],[2,3,4])
        self.assertEqual(sum(g['shortage'] for g in groups),0)

    def test_customer_completion_location_stays_frozen(self):
        from .services import release_selected
        order=self.customer_order()
        release_selected(allocation_ids=list(order.allocations.values_list('pk',flat=True)),shipped_at=timezone.now()-timedelta(minutes=1),actor=self.user)
        self.stock.location='CHANGED';self.stock.save()
        self.assertEqual(customer_print_groups(order)[0]['locations'][0]['location'],'A')

    def test_filtered_release_with_expected_stock_rolls_back_all(self):
        first=reserve(stock_id=self.stock.pk,project='같은호선',quantity=1,actor=self.user)
        expected=Stock.objects.create(item=self.item,location='예정',quantity=4,status='expected')
        reserve(stock_id=expected.pk,project='같은호선',quantity=1,actor=self.user)
        token=self.client.get('/allocations/?q=같은호선').context['filter_token']
        response=self.client.post('/allocations/?q=같은호선',dict(scope='filtered',filter_token=token,action='release',confirmed='on',shipped_at=(timezone.now()-timedelta(minutes=1)).isoformat()))
        self.assertEqual(response.status_code,200)
        self.assertFalse(Allocation.objects.filter(state='released').exists())
        first.refresh_from_db();self.assertEqual(first.state,'reserved')

    def test_nonstaff_cannot_use_bulk_customer_print_or_erp(self):
        order=self.customer_order()
        self.client.force_login(self.mobile)
        for url in ['/receipts/bulk/','/erp/upload/',f'/picking/{order.pk}/customer/','/allocations/']:
            self.assertEqual(self.client.get(url).status_code,302)
