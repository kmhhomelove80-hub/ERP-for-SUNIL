import uuid
from datetime import timedelta
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db.models import Sum
from django.test import TestCase
from django.utils import timezone
from .models import Item, Stock, Receipt, Allocation, Substitution, SubstitutionSettlement, PickingOrder
from .picking import parse_paste, plan_picking, confirm_picking
from .substitutions import reserve_substitution, dispatch_substitution, settle_substitution, cancel_substitution, confirm_company_erp, demand_balance, snapshot_summaries, repaid_quantity
from .services import receive_stock, release_selected, cancel_receipt, confirm_arrival
from .reconciliation import create_snapshot


class SubstitutionTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_user('loan-manager',is_staff=True)
        self.mobile=get_user_model().objects.create_user('mobile')
        self.a=Item.objects.create(code='REQUEST-A',name='고객 요청 등')
        self.b=Item.objects.create(code='ACTUAL-B',name='대체 등')
        self.sa=Stock.objects.create(item=self.a,location='OLD-A',quantity=5)
        self.sb=Stock.objects.create(item=self.b,location='OLD-B',quantity=5)
        rows=parse_paste('REQUEST-A\t10')
        self.order=confirm_picking(request_id=uuid.uuid4(),project='호선4283',rows=rows,preview=plan_picking(rows),allow_shortage=True,actor=self.user)
        self.client.force_login(self.user)

    def borrow(self, quantity=5, request_id=None):
        return reserve_substitution(request_id=request_id or uuid.uuid4(),order_id=self.order.pk,requested=self.a.code,stock_id=self.sb.pk,quantity=quantity,reason='동일 제품 / 후크 수정',actor=self.user)

    def dispatch(self, record=None):
        record=record or self.borrow()
        dispatch_substitution(substitution_id=record.pk,shipped_at=timezone.now()-timedelta(minutes=1),product_confirmed=True,actor=self.user)
        record.refresh_from_db()
        return record

    def receipt(self, quantity=5, item=None, location='NEW-RECEIPT'):
        return receive_stock(item_id=(item or self.a).pk,location=location,quantity=quantity,status='received',expected_date=None,sender='',request_id=uuid.uuid4(),actor=self.user)

    def repay(self, record, receipt, quantity=5, request_id=None):
        return settle_substitution(request_id=request_id or uuid.uuid4(),substitution_id=record.pk,receipt_id=receipt.pk,quantity=quantity,note='동일 제품 확인 / 필요한 후크 수정 완료',product_confirmed=True,actor=self.user)

    def test_reservation_is_explicit_idempotent_and_fills_demand(self):
        request_id=uuid.uuid4();record=self.borrow(request_id=request_id)
        self.assertEqual(self.borrow(request_id=request_id).pk,record.pk)
        self.assertEqual(demand_balance(self.order)[0]['shortage'],0)
        self.sb.refresh_from_db();self.assertEqual((self.sb.quantity,self.sb.reserved),(5,5))
        self.assertEqual(Substitution.objects.count(),1)
        with self.assertRaises(ValidationError):self.borrow(quantity=1)

    def test_same_code_no_capacity_missing_reason_and_nonstaff_rejected(self):
        data=dict(request_id=uuid.uuid4(),order_id=self.order.pk,requested=self.a.code,stock_id=self.sb.pk,quantity=5,reason='확인',actor=self.user)
        for change in [dict(stock_id=self.sa.pk),dict(quantity=6),dict(reason=''),dict(actor=self.mobile),dict(requested='NOT-REQUESTED')]:
            with self.subTest(change=change),self.assertRaises(ValidationError):reserve_substitution(**(data|change))
        self.assertFalse(Substitution.objects.exists())

    def test_normal_release_cannot_process_a_substitution(self):
        record=self.borrow()
        ids=list(self.order.allocations.values_list('pk',flat=True))
        with self.assertRaisesMessage(ValidationError,'일반 Release'):
            release_selected(allocation_ids=ids,shipped_at=timezone.now()-timedelta(minutes=1),actor=self.user)
        self.assertFalse(Allocation.objects.filter(state='released').exists())
        self.sb.refresh_from_db();self.assertEqual(self.sb.quantity,5)

    def test_dispatch_reduces_physical_once_and_is_not_release(self):
        record=self.borrow();when=timezone.now()-timedelta(minutes=1)
        for _ in range(2):dispatch_substitution(substitution_id=record.pk,shipped_at=when,product_confirmed=True,actor=self.user)
        self.sb.refresh_from_db();record.allocation.refresh_from_db()
        self.assertEqual((self.sb.quantity,self.sb.reserved),(0,0))
        self.assertEqual(record.allocation.state,'substituted')
        self.assertIsNone(record.allocation.released_at)
        self.assertEqual(record.allocation.completed_details['location'],'OLD-B')

    def test_expected_and_unconfirmed_product_cannot_dispatch(self):
        record=self.borrow()
        with self.assertRaises(ValidationError):dispatch_substitution(substitution_id=record.pk,shipped_at=timezone.now(),product_confirmed=False,actor=self.user)
        self.sb.status='expected';self.sb.save()
        with self.assertRaises(ValidationError):self.dispatch(record)
        self.sb.refresh_from_db();self.assertEqual(self.sb.quantity,5)

    def test_before_dispatch_cancel_releases_reservation_and_keeps_history(self):
        record=self.borrow()
        cancel_substitution(substitution_id=record.pk,note='출고 계획 취소',actor=self.user)
        self.sb.refresh_from_db();self.assertEqual((self.sb.quantity,self.sb.reserved),(5,0))
        self.assertEqual(demand_balance(self.order)[0]['shortage'],5)
        self.assertTrue(Substitution.objects.filter(pk=record.pk).exists())
        with self.assertRaises(ValidationError):self.dispatch(record)

    def test_after_dispatch_cannot_delete_or_cancel_borrowed_stock(self):
        record=self.dispatch()
        with self.assertRaises(ValidationError):cancel_substitution(substitution_id=record.pk,note='취소',actor=self.user)
        self.sb.refresh_from_db();self.assertEqual(self.sb.quantity,0)

    def test_receipt_repayment_conserves_total_and_uses_new_location(self):
        record=self.dispatch();receipt=self.receipt()
        before=Stock.objects.aggregate(q=Sum('quantity'))['q']
        settlement=self.repay(record,receipt)
        after=Stock.objects.aggregate(q=Sum('quantity'))['q']
        self.assertEqual(before,after)
        self.assertEqual(settlement.source_stock.quantity,0)
        self.assertEqual(settlement.target_stock.quantity,5)
        self.assertEqual(settlement.target_stock.location,'NEW-RECEIPT')
        self.assertEqual(settlement.target_stock.item_id,self.b.pk)
        self.sb.refresh_from_db();self.assertEqual(self.sb.quantity,0)
        record.allocation.refresh_from_db();self.assertEqual(record.allocation.state,'settled')
        self.assertEqual(record.allocation.completed_details['location'],'OLD-B')

    def test_partial_repayments_and_replay_do_not_create_stock(self):
        record=self.dispatch();receipt=self.receipt()
        request_id=uuid.uuid4();first=self.repay(record,receipt,quantity=3,request_id=request_id)
        self.assertEqual(self.repay(record,receipt,quantity=3,request_id=request_id).pk,first.pk)
        record.allocation.refresh_from_db();self.assertEqual(record.allocation.state,'substituted')
        self.assertEqual(repaid_quantity(record),3)
        self.repay(record,receipt,quantity=2)
        self.assertEqual(repaid_quantity(record),5)
        self.assertEqual(SubstitutionSettlement.objects.count(),2)
        with self.assertRaises(ValidationError):self.repay(record,receipt,quantity=1)

    def test_wrong_receipt_reserved_quantity_and_receipt_reuse_rejected(self):
        record=self.dispatch();wrong=self.receipt(item=self.b)
        with self.assertRaises(ValidationError):self.repay(record,wrong)
        correct=self.receipt()
        stock=correct.stock;stock.reserved=4;stock.save()
        with self.assertRaises(ValidationError):self.repay(record,correct,quantity=2)
        stock.reserved=0;stock.save()
        self.repay(record,correct)
        with self.assertRaises(ValidationError):cancel_receipt(receipt_id=correct.pk,reason='취소',actor=self.user)

    def test_receipt_before_borrow_is_not_production_repayment(self):
        receipt=self.receipt()
        Receipt.objects.filter(pk=receipt.pk).update(created_at=timezone.now()-timedelta(days=1))
        record=self.dispatch()
        with self.assertRaisesMessage(ValidationError,'대체 출고 후'):self.repay(record,receipt)

    def test_expected_prebook_arrival_after_dispatch_is_valid_repayment(self):
        receipt=receive_stock(item_id=self.a.pk,location='예정',quantity=5,status='expected',expected_date=timezone.localdate(),sender='생산',request_id=uuid.uuid4(),actor=self.user)
        record=self.dispatch()
        confirm_arrival(stock_id=receipt.stock_id,location='ARRIVAL-NEW',actor=self.user)
        receipt.refresh_from_db();self.assertIsNotNone(receipt.arrival_stock_id)
        settlement=self.repay(record,receipt)
        self.assertEqual(settlement.location,'ARRIVAL-NEW')

    def test_settlement_overflow_and_audit_failure_roll_back_all_changes(self):
        record=self.dispatch();receipt=self.receipt()
        Stock.objects.create(item=self.b,location='NEW-RECEIPT',quantity=2147483647)
        with self.assertRaises(ValidationError):self.repay(record,receipt)
        self.assertFalse(SubstitutionSettlement.objects.exists())
        receipt.stock.refresh_from_db();self.assertEqual(receipt.stock.quantity,5)
        Stock.objects.filter(item=self.b,location='NEW-RECEIPT').update(quantity=0)
        with patch('inventory.substitutions.audit',side_effect=RuntimeError('audit unavailable')):
            with self.assertRaises(RuntimeError):self.repay(record,receipt)
        self.assertFalse(SubstitutionSettlement.objects.exists())
        record.refresh_from_db();self.assertIsNone(record.settled_at)
        receipt.stock.refresh_from_db();self.assertEqual(receipt.stock.quantity,5)

    def test_erp_check_requires_repayment_and_never_changes_inventory(self):
        record=self.dispatch()
        with self.assertRaises(ValidationError):confirm_company_erp(substitution_id=record.pk,confirmed=True,note='ERP 확인',actor=self.user)
        self.repay(record,self.receipt())
        before=list(Stock.objects.values_list('pk','quantity','reserved'))
        confirm_company_erp(substitution_id=record.pk,confirmed=True,note='생산 입고 및 ERP 코드 수량 확인',actor=self.user)
        record.refresh_from_db();self.assertIsNotNone(record.erp_confirmed_at)
        confirm_company_erp(substitution_id=record.pk,confirmed=False,note='자료 재확인 필요',actor=self.user)
        record.refresh_from_db();self.assertIsNone(record.erp_confirmed_at)
        self.assertEqual(before,list(Stock.objects.values_list('pk','quantity','reserved')))

    def snapshot(self):
        plan=dict(digest=uuid.uuid4().hex,source_name='synthetic',rows=[dict(warehouse='Y09',code=self.a.code,name='요청',quantity=-5,shipping_pending=0),dict(warehouse='Y09',code=self.b.code,name='대체',quantity=5,shipping_pending=0)])
        return create_snapshot(request_id=uuid.uuid4(),label='회사 ERP',erp_as_of=timezone.now(),plan=plan,actor=self.user)

    def test_user_example_company_and_physical_balances_close_after_repayment(self):
        normal=list(self.order.allocations.values_list('pk',flat=True))
        release_selected(allocation_ids=normal,shipped_at=timezone.now()-timedelta(minutes=1),actor=self.user)
        record=self.dispatch()
        before=self.snapshot()
        self.assertEqual(before.rows.get(code=self.a.code).difference,5)
        self.assertEqual(before.rows.get(code=self.b.code).difference,-5)
        receipt=self.receipt()
        self.assertEqual(Stock.objects.aggregate(q=Sum('quantity'))['q'],5)
        self.repay(record,receipt)
        plan=dict(digest=uuid.uuid4().hex,source_name='after',rows=[dict(warehouse='Y09',code=self.a.code,name='요청',quantity=0,shipping_pending=0),dict(warehouse='Y09',code=self.b.code,name='대체',quantity=5,shipping_pending=0)])
        after=create_snapshot(request_id=uuid.uuid4(),label='상환 후 ERP',erp_as_of=timezone.now(),plan=plan,actor=self.user)
        self.assertTrue(all(r.difference==0 for r in after.rows.all()))
        self.assertEqual(snapshot_summaries(after,[self.a.code])[self.a.code]['outstanding'],0)
        self.assertEqual(before.rows.get(code=self.a.code).difference,5)

    def test_snapshot_links_both_codes_and_does_not_rewrite_old_balance(self):
        record=self.dispatch();snapshot=self.snapshot()
        before=list(snapshot.rows.values_list('code','erp_quantity','received'))
        summaries=snapshot_summaries(snapshot,[self.a.code,self.b.code])
        self.assertEqual(summaries[self.a.code]['outstanding'],5)
        self.assertEqual(summaries[self.b.code]['outstanding'],5)
        self.repay(record,self.receipt())
        self.assertEqual(snapshot_summaries(snapshot,[self.a.code])[self.a.code]['outstanding'],5)
        self.assertEqual(before,list(snapshot.rows.values_list('code','erp_quantity','received')))
        response=self.client.get(f'/erp/{snapshot.pk}/')
        self.assertContains(response,'기준시각 미상환 5개')
        self.assertContains(self.client.get(f'/substitutions/?code={self.a.code}&snapshot={snapshot.pk}'),'호선4283')

    def test_future_borrow_does_not_appear_in_past_snapshot(self):
        snapshot=self.snapshot();self.dispatch()
        self.assertEqual(dict(snapshot_summaries(snapshot,[self.a.code])),{})

    def test_full_shortage_draft_order_can_borrow_without_fake_stock(self):
        rows=parse_paste('MISSING-CODE\t2')
        with self.assertRaises(ValidationError):confirm_picking(request_id=uuid.uuid4(),project='전량 부족',rows=rows,preview=plan_picking(rows),allow_shortage=True,actor=self.user)
        order=confirm_picking(request_id=uuid.uuid4(),project='전량 부족',rows=rows,preview=plan_picking(rows),allow_shortage=True,actor=self.user,substitution_draft=True)
        record=reserve_substitution(request_id=uuid.uuid4(),order_id=order.pk,requested='MISSING-CODE',stock_id=self.sb.pk,quantity=2,reason='제품 동등 확인',actor=self.user)
        self.assertEqual(record.requested_code,'MISSING-CODE')
        self.assertFalse(Item.objects.filter(code='MISSING-CODE').exists())

    def test_customer_print_keeps_requested_code_and_marks_actual_borrow(self):
        from .customer_picking import customer_print_groups
        self.order.payload['customer_rows']=[dict(por='P',hull='4283',block='B',name='고객 제품',code=self.a.code,quantity=10)];self.order.save()
        record=self.borrow()
        group=customer_print_groups(self.order)[0]
        self.assertEqual(group['shortage'],0)
        self.assertEqual(group['locations'][1]['actual_code'],self.b.code)
        self.assertContains(self.client.get(f'/picking/{self.order.pk}/customer/'),'대체 ACTUAL-B')
        self.dispatch(record);self.repay(record,self.receipt())
        self.assertEqual(customer_print_groups(self.order)[0]['locations'][1]['location'],'OLD-B')

    def test_web_flow_and_permissions(self):
        response=self.client.post(f'/picking/{self.order.pk}/substitute/',dict(request_id=uuid.uuid4(),requested=self.a.code,stock=self.sb.pk,quantity=5,reason='동일 제품',confirmed='on'))
        self.assertEqual(response.status_code,302)
        record=Substitution.objects.get()
        url=response.url
        response=self.client.post(url,dict(action='dispatch',shipped_at=(timezone.now()-timedelta(minutes=1)).isoformat(),product_confirmed='on'))
        self.assertEqual(response.status_code,302)
        receipt=self.receipt()
        response=self.client.post(url,dict(action='settle',request_id=uuid.uuid4(),receipt=receipt.pk,quantity=5,note='동일/수정 완료',product_confirmed='on'))
        self.assertEqual(response.status_code,302)
        self.assertEqual(self.client.post(url,dict(action='erp',decision='confirm',note='ERP 확인',confirmed='on')).status_code,302)
        self.assertContains(self.client.get(url),'동일/수정 완료')
        self.client.force_login(self.mobile)
        for path in [url,'/substitutions/',f'/picking/{self.order.pk}/substitute/']:
            self.assertEqual(self.client.get(path).status_code,302)

    def test_readiness_and_normal_release_list_separate_substitutions(self):
        record=self.borrow()
        response=self.client.get('/allocations/')
        ids=[a.pk for a in response.context['allocations']]
        self.assertNotIn(record.allocation_id,ids)
        self.dispatch(record);self.repay(record,self.receipt())
        from .readiness import readiness_report
        report=readiness_report()
        self.assertTrue(report['ready'])
