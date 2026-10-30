import uuid
from datetime import datetime,timezone as datetime_timezone
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db.models import Sum
from django.test import TestCase, Client
from django.urls import reverse
from .models import Stock, Item, MovementRequest, AuditEvent
from .services import submit_movement, process_movement, reserve

class MovementUITests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user('mobile')
        self.other = get_user_model().objects.create_user('other')
        self.admin = get_user_model().objects.create_user('manager',is_staff=True)
        self.item = Item.objects.create(code='MOVE-CODE',name='등기구')
        self.source = Stock.objects.create(item=self.item,location='A01',quantity=20)
        self.token = uuid.uuid4()
    def submit(self, **overrides):
        data = dict(request_id=self.token,source_id=self.source.pk,destination='B02',quantity=5,actor=self.owner)
        data.update(overrides)
        return submit_movement(**data)
    def payload(self):
        return dict(request_id=str(self.token),destination='B02',quantity=5,confirmed='on')
    def test_request_does_not_move_stock_and_duplicate_submission_is_safe(self):
        request = self.submit()
        self.assertEqual(self.submit().pk,request.pk)
        self.source.refresh_from_db()
        self.assertEqual((self.source.quantity,self.source.reserved),(20,0))
        self.assertEqual(request.state,'pending')
        self.assertEqual(MovementRequest.objects.count(),1)
        self.assertEqual(request.snapshot['code'],'MOVE-CODE')
        with self.assertRaises(ValidationError): self.submit(quantity=6)
    def test_request_rejects_invalid_quantity_status_and_same_location(self):
        for data in [dict(quantity=0),dict(quantity=-1),dict(quantity=21),dict(quantity=True),dict(destination='a01'),dict(destination='')]:
            with self.subTest(data=data),self.assertRaises(ValidationError): self.submit(**data)
        self.source.status='expected';self.source.save()
        with self.assertRaises(ValidationError):self.submit()
        self.assertFalse(MovementRequest.objects.exists())
    def test_processing_checks_current_reservations_and_records_processor(self):
        request=self.submit(quantity=15)
        reserve(stock_id=self.source.pk,project='호선',quantity=10,actor=self.admin)
        process_movement(request_id=request.pk,actor=self.admin)
        request.refresh_from_db();self.source.refresh_from_db()
        self.assertEqual(request.state,'rejected')
        self.assertEqual(request.processed_by,self.admin)
        self.assertIsNotNone(request.processed_at)
        self.assertEqual(self.source.quantity,20)
        self.assertEqual(Stock.objects.count(),1)
    def test_manual_rejection_is_audited_without_stock_change(self):
        request=self.submit()
        process_movement(request_id=request.pk,actor=self.admin,rejection_reason='요청 위치 재확인')
        request.refresh_from_db()
        self.assertEqual((request.state,request.reason),('rejected','요청 위치 재확인'))
        self.assertEqual(AuditEvent.objects.get(action='movement').detail['reason'],'요청 위치 재확인')
        self.assertEqual(Stock.objects.get().quantity,20)
    def test_stock_update_and_status_roll_back_if_audit_fails(self):
        request=self.submit()
        with patch('inventory.services.audit',side_effect=RuntimeError('audit unavailable')):
            with self.assertRaises(RuntimeError):process_movement(request_id=request.pk,actor=self.admin)
        request.refresh_from_db();self.source.refresh_from_db()
        self.assertEqual((request.state,self.source.quantity),('pending',20))
        self.assertEqual(Stock.objects.count(),1)
    def test_mobile_web_request_and_admin_processing_preserve_total(self):
        self.client.force_login(self.owner)
        create=reverse('movement-create',args=[self.source.pk])
        self.assertContains(self.client.get(create),'현재 위치')
        response=self.client.post(create,self.payload())
        self.assertEqual(response.status_code,302)
        self.assertEqual(self.client.post(create,self.payload()).status_code,302)
        self.assertEqual(MovementRequest.objects.count(),1)
        detail=response.url
        self.assertEqual(self.client.post(detail,dict(action='process',confirmed='on')).status_code,403)
        self.client.force_login(self.admin)
        response=self.client.post(detail,dict(action='process',confirmed='on'))
        self.assertEqual(response.status_code,302)
        self.assertContains(self.client.get(detail),'완료')
        self.assertEqual(Stock.objects.aggregate(total=Sum('quantity'))['total'],20)
        self.assertEqual(Stock.objects.get(location='B02').quantity,5)
        self.client.post(detail,dict(action='process',confirmed='on'))
        self.assertEqual(Stock.objects.get(location='B02').quantity,5)
    def test_list_and_detail_do_not_expose_other_users_requests(self):
        request=self.submit()
        self.client.force_login(self.other)
        self.assertNotContains(self.client.get(reverse('movement-list')),'MOVE-CODE')
        self.assertEqual(self.client.get(reverse('movement-detail',args=[request.pk])).status_code,403)
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse('movement-list')),'MOVE-CODE')
    def test_post_requires_csrf_and_confirmation(self):
        client=Client(enforce_csrf_checks=True);client.force_login(self.owner)
        url=reverse('movement-create',args=[self.source.pk])
        self.assertEqual(client.post(url,self.payload()).status_code,403)
        self.client.force_login(self.owner)
        payload=self.payload();payload.pop('confirmed')
        self.assertContains(self.client.post(url,payload),'필수')
        self.assertFalse(MovementRequest.objects.exists())
    def test_staff_rejection_requires_reason(self):
        request=self.submit();self.client.force_login(self.admin)
        response=self.client.post(reverse('movement-detail',args=[request.pk]),dict(action='reject',confirmed='on'))
        self.assertContains(response,'거부 사유를 입력하세요.')
        request.refresh_from_db();self.assertEqual(request.state,'pending')
    def test_history_filters_korean_search_date_and_permission(self):
        request=self.submit()
        process_movement(request_id=request.pk,actor=self.admin,rejection_reason='위치 확인중')
        event=AuditEvent.objects.get(action='movement')
        AuditEvent.objects.filter(pk=event.pk).update(created_at=datetime(2026,10,30,23,30,tzinfo=datetime_timezone.utc))
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(reverse('history-list')).status_code,302)
        self.client.force_login(self.admin)
        response=self.client.get(reverse('history-list'),dict(q='확인중',action='movement',start='2026-10-31',end='2026-10-31'))
        self.assertContains(response,'위치 확인중')
        self.assertContains(response,'2026-10-31 08:30:00')
        self.assertEqual(response.context['page_obj'].paginator.count,1)
        response=self.client.get(reverse('history-list'),dict(start='2026-11-01',end='2026-10-01'))
        self.assertContains(response,'종료일은 시작일 이후')
