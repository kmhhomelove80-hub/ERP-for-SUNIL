import uuid
from datetime import timedelta
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.core import signing
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from .models import Item, Stock, PickingOrder, Allocation
from .picking import parse_paste, plan_picking, confirm_picking
from .services import reserve

class PickingTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('picker',is_staff=True)
        self.item = Item.objects.create(code='CODE1',name='모델')
        self.s1 = Stock.objects.create(item=self.item,location='A01',quantity=5)
        self.s2 = Stock.objects.create(item=self.item,location='예정',status='expected',quantity=8)
        self.rows = parse_paste('CODE1\t10')
    def confirm(self,rows=None,preview=None,token=None,allow_shortage=False):
        rows = rows or self.rows
        return confirm_picking(request_id=token or uuid.uuid4(),project='호선1',rows=rows,preview=preview or plan_picking(rows),allow_shortage=allow_shortage,actor=self.user)
    def test_parse_aggregates_and_rejects_bad_quantities(self):
        self.assertEqual(parse_paste('CODE1\t2\n\nCODE1\t3'),[{'code':'CODE1','quantity':5}])
        for value in ['CODE1\t-1','CODE1\t0','CODE1\t1.5','CODE1\t2\textra','ERP 코드\t수량','CODE1\t2147483648','CODE1\t'+'9'*5000]:
            with self.subTest(value=value), self.assertRaises(ValidationError): parse_paste(value)
    def test_split_allocation_includes_expected_and_preview_has_no_mutations(self):
        preview = plan_picking(self.rows)
        self.assertEqual([r['quantity'] for r in preview['allocations']],[5,5])
        self.assertEqual(preview['allocations'][1]['status'],'expected')
        self.assertFalse(Allocation.objects.exists())
        order = self.confirm(preview=preview)
        self.assertEqual(order.allocations.count(),2)
        self.s1.refresh_from_db();self.s2.refresh_from_db()
        self.assertEqual((self.s1.reserved,self.s2.reserved),(5,5))
    def test_shortage_requires_acknowledgement_and_unknown_code_is_reported(self):
        rows = parse_paste('CODE1\t20\nUNKNOWN\t2')
        preview = plan_picking(rows)
        self.assertEqual([r['shortage'] for r in preview['shortages']],[7,2])
        with self.assertRaises(ValidationError): self.confirm(rows=rows,preview=preview)
        self.assertFalse(PickingOrder.objects.exists())
        self.confirm(rows=rows,preview=preview,allow_shortage=True)
        self.assertEqual(Allocation.objects.count(),2)
    def test_stale_preview_is_rejected(self):
        preview = plan_picking(self.rows)
        reserve(stock_id=self.s1.pk,project='다른호선',quantity=2,actor=self.user)
        with self.assertRaises(ValidationError): self.confirm(preview=preview)
        self.assertFalse(PickingOrder.objects.exists())
    def test_confirm_is_idempotent(self):
        token, preview = uuid.uuid4(),plan_picking(self.rows)
        first = self.confirm(token=token,preview=preview)
        self.assertEqual(self.confirm(token=token,preview=preview).pk,first.pk)
        self.assertEqual(Allocation.objects.count(),2)
    def test_logging_failure_rolls_back_entire_order(self):
        with patch('inventory.picking.audit',side_effect=RuntimeError('audit unavailable')):
            with self.assertRaises(RuntimeError): self.confirm()
        self.assertFalse(PickingOrder.objects.exists())
        self.s1.refresh_from_db();self.assertEqual(self.s1.reserved,0)
    def test_signed_preview_web_flow_and_tamper_rejection(self):
        self.client.force_login(self.user)
        url = reverse('picking-create')
        response = self.client.post(url,{'action':'preview','project':'호선1','request_id':str(uuid.uuid4()),'pasted_rows':'10\tCODE1'})
        self.assertContains(response,'위치별 배정 미리보기')
        token = response.context['confirm_form']['preview_token'].value()
        self.assertFalse(PickingOrder.objects.exists())
        response = self.client.post(url,{'action':'confirm','preview_token':token+'tamper'})
        self.assertContains(response,'유효하지 않습니다')
        response = self.client.post(url,{'action':'confirm','preview_token':token})
        self.assertEqual(response.status_code,302)
        detail = self.client.get(response.url)
        self.assertContains(detail,'호선1')
        self.assertContains(detail,'인쇄 / PDF로 저장')
    def test_other_user_cannot_confirm_preview(self):
        self.client.force_login(self.user)
        token = signing.dumps(dict(actor=self.user.pk+100),salt='picking-preview')
        response = self.client.post(reverse('picking-create'),{'action':'confirm','preview_token':token})
        self.assertContains(response,'다른 사용자의 미리보기')
    def test_expired_preview_cannot_be_confirmed(self):
        self.client.force_login(self.user)
        import time
        now = time.time()
        with patch('django.core.signing.time.time',return_value=now-1801):
            token = signing.dumps(dict(actor=self.user.pk),salt='picking-preview')
        response = self.client.post(reverse('picking-create'),{'action':'confirm','preview_token':token})
        self.assertContains(response,'만료되었거나')
        self.assertFalse(PickingOrder.objects.exists())
    def test_selective_release_web_form_and_expected_block(self):
        order = self.confirm()
        first = order.allocations.get(stock=self.s1)
        expected = order.allocations.get(stock=self.s2)
        self.client.force_login(self.user)
        url = reverse('allocation-list')
        response = self.client.post(url,{'action':'release','allocations':[str(expected.pk)],'shipped_at':timezone.localtime(timezone.now()-timedelta(minutes=1)).strftime('%Y-%m-%dT%H:%M'),'confirmed':'on'})
        self.assertContains(response,'입고 완료 후')
        self.assertEqual(self.client.post(url,{'action':'release','allocations':[str(first.pk)],'shipped_at':'2026-01-01T10:00','confirmed':'on'}).status_code,302)
        first.refresh_from_db();expected.refresh_from_db()
        self.assertEqual((first.state,expected.state),('released','reserved'))
        self.assertEqual(self.client.post(url,{'action':'cancel','allocations':[str(expected.pk)],'confirmed':'on'}).status_code,302)
        self.s2.refresh_from_db();self.assertEqual(self.s2.reserved,0)
