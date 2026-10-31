import uuid
from django.contrib.auth import get_user_model
from django.test import TestCase
from .models import Item,Stock
from .picking import parse_paste,plan_picking,confirm_picking
from .picking_totals import picking_totals,order_totals
from .services import cancel_selected
from .substitutions import reserve_substitution


class PickingQuantityTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_user('quantity-check',is_staff=True)
        self.a=Item.objects.create(code='QTY-A')
        Stock.objects.create(item=self.a,location='A1',quantity=3)
        Stock.objects.create(item=self.a,location='A2',quantity=2)
        self.client.force_login(self.user)

    def order(self,quantity=8):
        rows=parse_paste(f'QTY-A\t{quantity}')
        return confirm_picking(request_id=uuid.uuid4(),project='수량 대조',rows=rows,preview=plan_picking(rows),allow_shortage=True,actor=self.user)

    def test_preview_duplicate_codes_and_multiple_locations_sum_quantities(self):
        response=self.client.post('/picking/new/',dict(action='preview',request_id=uuid.uuid4(),project='수량 대조',pasted_rows='4\tQTY-A\n4\tQTY-A'))
        self.assertEqual(response.context['totals']['requested'],8)
        self.assertEqual(response.context['totals']['allocated'],5)
        self.assertEqual(response.context['totals']['shortage'],3)
        self.assertContains(response,'요청 수량 확인 필요')
        self.assertContains(response,'요청 총수량 8개')
        self.assertContains(response,'피킹 배정 총수량 5개')

    def test_cancelled_rows_excluded_from_both_print_formats(self):
        order=self.order()
        cancel_selected(allocation_ids=[order.allocations.get(stock__location='A1').pk],actor=self.user)
        self.assertEqual(order_totals(order)['allocated'],2)
        self.assertEqual(order_totals(order)['shortage'],6)
        order.payload['customer_rows']=[dict(por='P',hull='H',block='B',name='품목',code='QTY-A',quantity=8)];order.save()
        for url in [f'/picking/{order.pk}/',f'/picking/{order.pk}/customer/']:
            response=self.client.get(url)
            self.assertContains(response,'피킹 배정 총수량 2개')
            self.assertEqual(response.context['totals']['requested'],8)

    def test_substitution_counts_once_against_requested_code(self):
        order=self.order()
        item=Item.objects.create(code='QTY-B');stock=Stock.objects.create(item=item,location='B1',quantity=3)
        reserve_substitution(request_id=uuid.uuid4(),order_id=order.pk,requested='QTY-A',stock_id=stock.pk,quantity=3,reason='동일 제품',actor=self.user)
        total=order_totals(order)
        self.assertEqual((total['requested'],total['normal'],total['substitute'],total['allocated']),(8,5,3,8))
        self.assertTrue(total['matches'])
        self.assertContains(self.client.get(f'/picking/{order.pk}/'),'요청 수량과 일치')

    def test_equal_grand_total_wrong_codes_are_not_reported_as_matching(self):
        total=picking_totals([dict(code='A',quantity=5)], [dict(code='B',quantity=5)])
        self.assertEqual(total['allocated'],5)
        self.assertFalse(total['matches'])
        self.assertEqual((total['shortage'],total['excess']),(5,5))

    def test_missing_original_request_is_not_reported_as_zero(self):
        total=picking_totals(None,[dict(code='A',quantity=5)])
        self.assertIsNone(total['requested']);self.assertFalse(total['matches'])
