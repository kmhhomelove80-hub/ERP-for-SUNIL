from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import connection, connections, close_old_connections
from django.test import TransactionTestCase
from .models import Item, Stock, MovementRequest, Allocation
from .services import reserve, process_movement

@skipUnless(connection.vendor == 'postgresql','실제 PostgreSQL에서만 동시성 검증')
class PostgreSQLConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('concurrent')
        self.item = Item.objects.create(code='CONCURRENT')
        self.stock = Stock.objects.create(item=self.item,location='A',quantity=10)
    def parallel(self,functions):
        barrier = Barrier(len(functions))
        def run(function):
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SET lock_timeout = '5s'")
                    cursor.execute("SET statement_timeout = '10s'")
                barrier.wait(timeout=5)
                return function()
            finally: connections.close_all()
        with ThreadPoolExecutor(max_workers=len(functions)) as executor:
            futures = [executor.submit(run,f) for f in functions]
            return [f.result(timeout=15) for f in futures]
    def test_competing_reservations_never_overallocate(self):
        def attempt():
            try:
                reserve(stock_id=self.stock.pk,project='호선',quantity=7,actor=self.user)
                return 'reserved'
            except ValidationError: return 'rejected'
        self.assertCountEqual(self.parallel([attempt,attempt]),['reserved','rejected'])
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.reserved,7)
        self.assertEqual(Allocation.objects.count(),1)
    def test_opposite_movements_do_not_deadlock_or_lose_quantity(self):
        other = Stock.objects.create(item=self.item,location='B',quantity=20)
        first = MovementRequest.objects.create(source=self.stock,destination='B',quantity=5,requested_by=self.user)
        second = MovementRequest.objects.create(source=other,destination='A',quantity=5,requested_by=self.user)
        self.parallel([lambda:process_movement(request_id=first.pk,actor=self.user),lambda:process_movement(request_id=second.pk,actor=self.user)])
        self.stock.refresh_from_db();other.refresh_from_db()
        self.assertEqual((self.stock.quantity,other.quantity),(10,20))
        self.assertEqual(MovementRequest.objects.filter(state='completed').count(),2)
    def test_same_movement_request_only_applies_once(self):
        request = MovementRequest.objects.create(source=self.stock,destination='B',quantity=4,requested_by=self.user)
        self.parallel([lambda:process_movement(request_id=request.pk,actor=self.user)]*2)
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.quantity,6)
        self.assertEqual(Stock.objects.get(location='B').quantity,4)
