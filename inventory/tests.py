from datetime import timedelta
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone
from .models import Item, Stock, Allocation, MovementRequest, AuditEvent
from .services import reserve, release_selected, cancel_allocation, process_movement

class WorkflowTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('operator', password='test-only-password')
        self.item = Item.objects.create(code='ERP-001', name='시험등기구')
        self.stock = Stock.objects.create(item=self.item, location='A0101', quantity=20)
    def allocate(self, quantity=5, stock=None):
        return reserve(stock_id=(stock or self.stock).pk, project='호선 5481', quantity=quantity, actor=self.user)
    def release(self, *allocations):
        release_selected(allocation_ids=[a.pk for a in allocations], shipped_at=timezone.now()-timedelta(minutes=1), actor=self.user)
    def test_expected_stock_can_be_reserved_but_not_released(self):
        self.stock.status = 'expected'
        self.stock.save()
        a = self.allocate()
        with self.assertRaises(ValidationError): self.release(a)
        self.stock.refresh_from_db()
        self.assertEqual((self.stock.quantity, self.stock.reserved), (20,5))
        self.assertFalse(AuditEvent.objects.filter(action='release').exists())
    def test_release_only_selected_rows_full_quantity_and_prevent_repeat(self):
        a,b = self.allocate(5), self.allocate(3)
        self.release(a)
        self.stock.refresh_from_db(); b.refresh_from_db()
        self.assertEqual((self.stock.quantity,self.stock.reserved,self.stock.available),(15,3,12))
        self.assertEqual(b.state,'reserved')
        with self.assertRaises(ValidationError): self.release(a)
        self.assertEqual(AuditEvent.objects.filter(action='release').count(),1)
    def test_mixed_invalid_batch_rolls_back_all_rows(self):
        a = self.allocate()
        s = Stock.objects.create(item=self.item, location='11/3 예정', status='expected', quantity=10)
        b = self.allocate(stock=s)
        with self.assertRaises(ValidationError): self.release(a,b)
        self.stock.refresh_from_db(); a.refresh_from_db()
        self.assertEqual(self.stock.quantity,20)
        self.assertEqual(a.state,'reserved')
    def test_reservation_cannot_exceed_available(self):
        self.allocate(18)
        with self.assertRaises(ValidationError): self.allocate(3)
        self.assertEqual(Allocation.objects.count(),1)
    def test_cancel_restores_available_without_changing_total(self):
        a = self.allocate()
        cancel_allocation(allocation_id=a.pk, actor=self.user)
        self.stock.refresh_from_db()
        self.assertEqual((self.stock.quantity,self.stock.reserved),(20,0))
        with self.assertRaises(ValidationError): cancel_allocation(allocation_id=a.pk,actor=self.user)
    def movement(self, quantity, destination='B0101'):
        return MovementRequest.objects.create(source=self.stock,destination=destination,quantity=quantity,requested_by=self.user)
    def test_movement_respects_reserved_stock_and_is_idempotent(self):
        self.allocate(5)
        r = self.movement(15)
        process_movement(request_id=r.pk,actor=self.user)
        process_movement(request_id=r.pk,actor=self.user)
        self.stock.refresh_from_db()
        self.assertEqual((self.stock.quantity,self.stock.reserved),(5,5))
        self.assertEqual(Stock.objects.get(location='B0101').quantity,15)
        self.assertEqual(AuditEvent.objects.filter(action='movement').count(),1)
    def test_rejected_request_does_not_change_stock(self):
        self.allocate(10)
        r = self.movement(11)
        process_movement(request_id=r.pk,actor=self.user)
        r.refresh_from_db(); self.stock.refresh_from_db()
        self.assertEqual(r.state,'rejected')
        self.assertTrue(r.reason)
        self.assertEqual(self.stock.quantity,20)
        self.assertEqual(Stock.objects.count(),1)
    def test_stock_search_requires_auth_and_filters_results(self):
        self.assertEqual(self.client.get('/').status_code,302)
        self.client.force_login(self.user)
        response = self.client.get('/',{'q':'ERP-001'})
        self.assertContains(response,'ERP-001')
        self.assertContains(response,'시험등기구')
        self.assertNotContains(self.client.get('/',{'q':'NO-MATCH'}),'시험등기구')
