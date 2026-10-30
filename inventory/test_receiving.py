import uuid
from datetime import date
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase, Client
from django.urls import reverse
from .models import Item, Stock, Allocation, Receipt, AuditEvent
from .services import receive_stock, confirm_arrival, reserve

class ReceivingTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('manager',password='test-only',is_staff=True)
        self.item = Item.objects.create(code='ERP-NEW',name='새 등기구')
        self.token = uuid.uuid4()
    def receive(self, **overrides):
        data = dict(item_id=self.item.pk,location='A01',quantity=10,status='received',expected_date=None,sender='',request_id=self.token,actor=self.user)
        data.update(overrides)
        return receive_stock(**data)
    def test_repeated_receipt_and_changed_payload(self):
        first = self.receive()
        self.assertEqual(self.receive().pk,first.pk)
        self.assertEqual(Stock.objects.get().quantity,10)
        self.assertEqual(Receipt.objects.count(),1)
        with self.assertRaises(ValidationError): self.receive(location='B01')
    def test_expected_arrival_merges_and_preserves_reservations(self):
        r = self.receive(status='expected',location='예정1',sender='담당자',expected_date=date(2026,11,3))
        a = reserve(stock_id=r.stock_id,project='호선1',quantity=4,actor=self.user)
        target = Stock.objects.create(item=self.item,location='B02',quantity=5)
        confirmed = confirm_arrival(stock_id=r.stock_id,location='B02',actor=self.user)
        a.refresh_from_db(); r.stock.refresh_from_db(); target.refresh_from_db()
        self.assertEqual(confirmed.pk,target.pk)
        self.assertEqual((target.quantity,target.reserved),(15,4))
        self.assertEqual((r.stock.quantity,r.stock.reserved),(0,0))
        self.assertEqual(a.stock_id,target.pk)
        with self.assertRaises(ValidationError): confirm_arrival(stock_id=r.stock_id,location='B02',actor=self.user)
    def test_audit_failure_rolls_back_receipt_and_stock(self):
        with patch('inventory.services.audit',side_effect=RuntimeError('audit unavailable')):
            with self.assertRaises(RuntimeError): self.receive()
        self.assertFalse(Stock.objects.exists())
        self.assertFalse(Receipt.objects.exists())
    def test_arrival_failure_rolls_back_entire_transition(self):
        r = self.receive(status='expected',sender='담당자',expected_date=date(2026,11,3))
        a = reserve(stock_id=r.stock_id,project='호선',quantity=4,actor=self.user)
        with patch('inventory.services.audit',side_effect=RuntimeError('audit unavailable')):
            with self.assertRaises(RuntimeError): confirm_arrival(stock_id=r.stock_id,location='B02',actor=self.user)
        r.stock.refresh_from_db(); a.refresh_from_db()
        self.assertEqual((r.stock.quantity,r.stock.reserved),(10,4))
        self.assertEqual(a.stock_id,r.stock_id)
        self.assertEqual(Stock.objects.count(),1)
    def test_receipt_form_requires_staff_and_csrf(self):
        url = reverse('receipt-create')
        self.assertEqual(self.client.get(url).status_code,302)
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        self.assertEqual(client.post(url,{}).status_code,403)
        response = client.get(url)
        self.assertContains(response,'입고')
        token = client.cookies['csrftoken'].value
        payload = dict(request_id=str(self.token),item=self.item.pk,status='received',location='A01',quantity=3,csrfmiddlewaretoken=token)
        self.assertEqual(client.post(url,payload).status_code,302)
        self.assertEqual(client.post(url,payload).status_code,302)
        self.assertEqual(Stock.objects.get().quantity,3)
    def test_expected_form_requires_sender_and_date(self):
        self.client.force_login(self.user)
        response = self.client.post(reverse('receipt-create'),dict(request_id=self.token,item=self.item.pk,status='expected',location='예정',quantity=3))
        self.assertContains(response,'예정 재고에는 필수입니다.')
        self.assertFalse(Receipt.objects.exists())
    def test_viewer_cannot_modify(self):
        viewer = get_user_model().objects.create_user('viewer')
        self.client.force_login(viewer)
        self.assertEqual(self.client.post(reverse('receipt-create'),{}).status_code,302)
        self.assertFalse(Receipt.objects.exists())
