from datetime import date
from unittest.mock import patch
import uuid
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from .models import Item,Stock,AuditEvent
from .services import receive_stock,reserve,confirm_arrival,cancel_receipt

class ReceiptCancelTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_user('receipt-manager',is_staff=True)
        self.item=Item.objects.create(code='CANCEL1')
    def receipt(self,expected=False):
        return receive_stock(item_id=self.item.pk,location='예정' if expected else 'A01',quantity=10,status='expected' if expected else 'received',expected_date=date(2026,11,3) if expected else None,sender='담당자' if expected else '',request_id=uuid.uuid4(),actor=self.user)
    def test_cancel_original_once_and_keep_audit(self):
        r=self.receipt();cancel_receipt(receipt_id=r.pk,reason='오입력',actor=self.user);cancel_receipt(receipt_id=r.pk,reason='재전송',actor=self.user)
        self.assertEqual(Stock.objects.get().quantity,0)
        self.assertEqual(AuditEvent.objects.filter(action='receipt_cancel').count(),1)
        self.assertEqual(Stock.objects.count(),1)
    def test_reserved_quantity_blocks_cancellation(self):
        r=self.receipt();reserve(stock_id=r.stock_id,project='호선',quantity=1,actor=self.user)
        with self.assertRaises(ValidationError):cancel_receipt(receipt_id=r.pk,reason='오입력',actor=self.user)
        r.refresh_from_db();self.assertIsNone(r.cancelled_at)
    def test_arrived_expected_receipt_cannot_be_cancelled_against_later_stock(self):
        r=self.receipt(True);confirm_arrival(stock_id=r.stock_id,location='A01',actor=self.user)
        self.receipt(True)
        with self.assertRaises(ValidationError):cancel_receipt(receipt_id=r.pk,reason='잘못된 취소',actor=self.user)
        self.assertEqual(Stock.objects.get(location='예정').quantity,10)
    def test_audit_failure_rolls_back_cancel(self):
        r=self.receipt()
        with patch('inventory.services.audit',side_effect=RuntimeError('audit unavailable')):
            with self.assertRaises(RuntimeError):cancel_receipt(receipt_id=r.pk,reason='오입력',actor=self.user)
        r.refresh_from_db();self.assertIsNone(r.cancelled_at);self.assertEqual(Stock.objects.get().quantity,10)
