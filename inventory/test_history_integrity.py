from datetime import timedelta,date
from unittest.mock import patch
import uuid
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone
from .models import Item,Stock,Allocation,PickingOrder
from .services import reserve,release_selected,cancel_allocation,confirm_arrival,receive_stock,submit_movement,process_movement,MAX_QUANTITY
from .picking import plan_picking,confirm_picking,parse_paste

class HistoryIntegrityTests(TestCase):
    def setUp(self):
        self.actor=get_user_model().objects.create_user('history-manager',is_staff=True)
        self.item=Item.objects.create(code='HIST1',name='원본 모델',specification='원본 사양')
        self.stock=Stock.objects.create(item=self.item,location='A01',quantity=10)
    def order(self):
        rows=parse_paste('HIST1\t3')
        return confirm_picking(request_id=uuid.uuid4(),project='호선 HISTORY',rows=rows,preview=plan_picking(rows),allow_shortage=False,actor=self.actor)
    def test_released_details_survive_master_edits_and_history_is_searchable(self):
        order=self.order();a=order.allocations.get()
        release_selected(allocation_ids=[a.pk],shipped_at=timezone.now()-timedelta(seconds=1),actor=self.actor)
        Item.objects.filter(pk=self.item.pk).update(name='변경 모델',specification='변경 사양')
        self.client.force_login(self.actor)
        response=self.client.get(f'/picking/{order.pk}/')
        self.assertContains(response,'원본 모델');self.assertNotContains(response,'변경 모델')
        a.refresh_from_db();self.assertEqual(a.completed_by,self.actor)
        self.assertEqual(a.completed_details['location'],'A01')
        response=self.client.get('/picking/',{'status':'released','q':'HIST1'})
        self.assertContains(response,'호선 HISTORY')
        self.assertContains(self.client.get('/allocation-history/',{'status':'released'}),'원본 모델')
    def test_cancel_preserves_details_and_legacy_seq_can_be_found(self):
        a=reserve(stock_id=self.stock.pk,project='기존호선',quantity=2,actor=self.actor)
        a.legacy_seq='2026103009';a.save()
        cancel_allocation(allocation_id=a.pk,actor=self.actor)
        Item.objects.filter(pk=self.item.pk).update(name='변경 모델')
        a.refresh_from_db();self.assertIsNotNone(a.cancelled_at)
        self.assertEqual(a.display_details['model'],'원본 모델')
        self.client.force_login(self.actor)
        self.assertContains(self.client.get('/allocation-history/',{'q':'2026103009'}),'기존호선')
    def test_expected_reservation_follows_arrival_but_assignment_is_preserved(self):
        self.stock.status='expected';self.stock.legacy_name='예정 원본 모델';self.stock.save()
        a=reserve(stock_id=self.stock.pk,project='호선',quantity=2,actor=self.actor)
        target=confirm_arrival(stock_id=self.stock.pk,location='B02',actor=self.actor)
        a.refresh_from_db()
        self.assertEqual(a.assigned_details['location'],'A01')
        self.assertEqual(a.display_details['location'],'B02')
        self.assertEqual(target.legacy_name,'예정 원본 모델')
        release_selected(allocation_ids=[a.pk],shipped_at=timezone.now()-timedelta(seconds=1),actor=self.actor)
        a.refresh_from_db();self.assertEqual(a.display_details['location'],'B02')
    def test_audit_failure_rolls_back_completion_details(self):
        a=reserve(stock_id=self.stock.pk,project='호선',quantity=2,actor=self.actor)
        with patch('inventory.services.audit',side_effect=RuntimeError('audit unavailable')):
            with self.assertRaises(RuntimeError):release_selected(allocation_ids=[a.pk],shipped_at=timezone.now()-timedelta(seconds=1),actor=self.actor)
        a.refresh_from_db();self.assertEqual(a.state,'reserved');self.assertEqual(a.completed_details,{})
    def test_order_list_counts_are_not_multiplied_by_code_search(self):
        extra=Stock.objects.create(item=self.item,location='B02',quantity=5)
        rows=parse_paste('HIST1\t12')
        order=confirm_picking(request_id=uuid.uuid4(),project='두 위치',rows=rows,preview=plan_picking(rows),allow_shortage=False,actor=self.actor)
        self.client.force_login(self.actor)
        response=self.client.get('/picking/',{'q':'HIST1','status':'active'})
        listed=next(o for o in response.context['page_obj'].object_list if o.pk==order.pk)
        self.assertEqual((listed.total_rows,listed.active_rows),(2,2))
    def test_receipt_rejects_fraction_bool_and_combined_overflow(self):
        data=dict(item_id=self.item.pk,location='A01',status='received',expected_date=None,sender='',request_id=uuid.uuid4(),actor=self.actor)
        for qty in [True,1.5,MAX_QUANTITY+1]:
            with self.subTest(qty=qty),self.assertRaises(ValidationError):receive_stock(quantity=qty,**data)
        self.stock.quantity=MAX_QUANTITY;self.stock.save()
        with self.assertRaises(ValidationError):receive_stock(quantity=1,**data)
    def test_target_overflow_rejects_movement_without_subtracting_source(self):
        Stock.objects.create(item=self.item,location='B02',quantity=MAX_QUANTITY)
        request=submit_movement(request_id=uuid.uuid4(),source_id=self.stock.pk,destination='B02',quantity=1,actor=self.actor)
        process_movement(request_id=request.pk,actor=self.actor)
        request.refresh_from_db();self.stock.refresh_from_db()
        self.assertEqual((request.state,self.stock.quantity),('rejected',10))

    def test_mobile_user_cannot_view_pc_picking_history(self):
        order=self.order()
        viewer=get_user_model().objects.create_user('mobile-only')
        self.client.force_login(viewer)
        for url in [f'/picking/{order.pk}/','/picking/','/allocation-history/']:
            self.assertEqual(self.client.get(url).status_code,302)
