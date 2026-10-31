import uuid
from datetime import timedelta
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from .models import Item,Stock,Allocation
from .services import reserve,release_selected,cancel_selected
from .picking import parse_paste,plan_picking,confirm_picking
from .substitutions import reserve_substitution


class MobileAllocationTests(TestCase):
    def setUp(self):
        self.manager=get_user_model().objects.create_user('manager',is_staff=True)
        self.mobile=get_user_model().objects.create_user('phone')
        self.item=Item.objects.create(code='MOBILE-ITEM',name='조회 품목',specification='사양 조회')
        self.stock=Stock.objects.create(item=self.item,location='A01',quantity=100)
        self.other=Stock.objects.create(item=self.item,location='B01',quantity=100)
        self.client.force_login(self.mobile)

    def test_normal_user_sees_current_projects_seq_and_quantity_only_at_this_location(self):
        first=reserve(stock_id=self.stock.pk,project='호선 4283 신청자',quantity=4,actor=self.manager)
        first.legacy_seq='2026103101';first.save()
        gone=reserve(stock_id=self.stock.pk,project='이미 출고',quantity=2,actor=self.manager)
        cancelled=reserve(stock_id=self.stock.pk,project='취소 물량',quantity=1,actor=self.manager)
        reserve(stock_id=self.other.pk,project='다른 위치',quantity=3,actor=self.manager)
        release_selected(allocation_ids=[gone.pk],shipped_at=timezone.now()-timedelta(minutes=1),actor=self.manager)
        cancel_selected(allocation_ids=[cancelled.pk],actor=self.manager)
        response=self.client.get(f'/stock/{self.stock.pk}/allocations/')
        self.assertContains(response,'호선 4283 신청자');self.assertContains(response,'2026103101')
        self.assertEqual(response.context['allocated_total'],4)
        self.assertTrue(response.context['matches'])
        for text in ['이미 출고','취소 물량','다른 위치']:
            self.assertNotContains(response,text)

    def test_totals_cover_all_pages_and_no_mutation_endpoint(self):
        for i in range(31):reserve(stock_id=self.stock.pk,project=f'호선{i}',quantity=1,actor=self.manager)
        response=self.client.get(f'/stock/{self.stock.pk}/allocations/')
        self.assertEqual(len(response.context['page_obj']),30)
        self.assertEqual(response.context['allocated_total'],31)
        self.assertEqual(response.context['page_obj'].paginator.count,31)
        self.assertEqual(self.client.post(f'/stock/{self.stock.pk}/allocations/',dict(quantity=999)).status_code,405)
        self.stock.refresh_from_db();self.assertEqual(self.stock.reserved,31)

    def test_missing_login_and_mismatch_are_handled_without_repair(self):
        self.stock.reserved=3;self.stock.save()
        response=self.client.get(f'/stock/{self.stock.pk}/allocations/')
        self.assertContains(response,'원장 allocated와 할당 합계가 다릅니다')
        self.stock.refresh_from_db();self.assertEqual(self.stock.reserved,3)
        self.client.logout()
        self.assertEqual(self.client.get(f'/stock/{self.stock.pk}/allocations/').status_code,302)

    def test_new_order_and_substitute_requested_code_visible_without_management_links(self):
        rows=parse_paste('CUSTOMER-CODE\t5')
        order=confirm_picking(request_id=uuid.uuid4(),project='신규 프로젝트',rows=rows,preview=plan_picking(rows),allow_shortage=True,actor=self.manager,substitution_draft=True)
        loan=reserve_substitution(request_id=uuid.uuid4(),order_id=order.pk,requested='CUSTOMER-CODE',stock_id=self.stock.pk,quantity=5,reason='동일 제품',actor=self.manager)
        response=self.client.get(f'/stock/{self.stock.pk}/allocations/')
        self.assertContains(response,'CUSTOMER-CODE');self.assertContains(response,order.number)
        self.assertContains(response,'manager')
        self.assertNotContains(response,f'href="/picking/{order.pk}/"')
        self.assertNotContains(response,f'href="/substitutions/{loan.pk}/"')

    def test_inventory_links_both_cards_and_desktop_to_readonly_allocation_details(self):
        reserve(stock_id=self.stock.pk,project='할당 프로젝트',quantity=4,actor=self.manager)
        response=self.client.get('/')
        self.assertContains(response,f'/stock/{self.stock.pk}/allocations/',count=2)
        self.assertContains(response,'4개 할당 내역')

    def test_mobile_stock_cards_do_not_contain_arrival_action_even_for_staff(self):
        Stock.objects.create(item=self.item,location='예정',quantity=3,status='expected')
        self.client.force_login(self.manager)
        html=self.client.get('/').content.decode()
        cards=html.split('<div class="mobile-stock-cards">',1)[1]
        self.assertNotIn('도착 확인',cards)
