import uuid
from django.conf import settings
from django.db import models
from django.db.models import Q, F

class Item(models.Model):
    code = models.CharField('ERP 코드', max_length=100, unique=True)
    name = models.CharField('모델명', max_length=250, blank=True)
    specification = models.TextField('사양', blank=True)
    def __str__(self): return self.code

class Stock(models.Model):
    class Status(models.TextChoices):
        EXPECTED = 'expected', '입고 예정'
        RECEIVED = 'received', '입고 완료'
    item = models.ForeignKey(Item, on_delete=models.PROTECT)
    warehouse = models.CharField(max_length=10, default='Y09')
    location = models.CharField('위치/임시 식별자', max_length=150)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.RECEIVED)
    quantity = models.PositiveIntegerField('현재고', default=0)
    reserved = models.PositiveIntegerField('allocated', default=0)
    expected_date = models.DateField(null=True, blank=True)
    sender = models.CharField(max_length=100, blank=True)
    legacy_name = models.CharField(max_length=250, blank=True)
    legacy_specification = models.TextField(blank=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['item', 'warehouse', 'location', 'status'], name='unique_stock_bucket'), models.CheckConstraint(condition=Q(quantity__gte=F('reserved')), name='stock_covers_reservations')]
    @property
    def available(self): return self.quantity - self.reserved

class PickingOrder(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.CharField('프로젝트/호선', max_length=250)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
    payload = models.JSONField(default=dict)
    shortages = models.JSONField(default=list)

    @property
    def number(self):
        from django.utils import timezone
        return f'P-{timezone.localtime(self.created_at):%Y%m%d}-{self.pk.hex[:12]}'


class Allocation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    stock = models.ForeignKey(Stock, on_delete=models.PROTECT)
    order = models.ForeignKey(PickingOrder, on_delete=models.PROTECT, null=True, blank=True, related_name='allocations')
    project = models.CharField('프로젝트/호선', max_length=250)
    legacy_seq = models.CharField('기존 SEQ', max_length=40, blank=True)
    quantity = models.PositiveIntegerField()
    state = models.CharField(max_length=12, choices=[('reserved','예약'),('released','출고 확정'),('cancelled','취소'),('substituted','대체 출고·미상환'),('settled','대체 상환 완료')], default='reserved')
    shipped_at = models.DateTimeField(null=True, blank=True)
    released_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True,blank=True)
    completed_by = models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,null=True,blank=True,related_name='completed_allocations')
    assigned_details = models.JSONField(default=dict)
    completed_details = models.JSONField(default=dict)
    @property
    def display_details(self):
        from .transaction_details import stock_details
        if self.state!='reserved' and self.completed_details:
            return dict(self.completed_details,recorded=True)
        return dict(stock_details(self.stock),recorded=self.state=='reserved')
    class Meta:
        constraints = [models.CheckConstraint(condition=Q(quantity__gt=0), name='allocation_positive')]

class MovementRequest(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    source = models.ForeignKey(Stock, on_delete=models.PROTECT)
    destination = models.CharField(max_length=150)
    quantity = models.PositiveIntegerField()
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    state = models.CharField(max_length=12, choices=[('pending','대기'),('completed','완료'),('rejected','거부')], default='pending')
    reason = models.TextField(blank=True)
    requested_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    processed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name='processed_movements')
    snapshot = models.JSONField(default=dict)
    class Meta:
        constraints = [models.CheckConstraint(condition=Q(quantity__gt=0), name='movement_positive')]

class AuditEvent(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    action = models.CharField(max_length=40)
    reference = models.CharField(max_length=100)
    detail = models.JSONField(default=dict)

class ImportBatch(models.Model):
    digest = models.CharField(max_length=64, unique=True)
    source_name = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    summary = models.JSONField(default=dict)

class Receipt(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Submitted UUID prevents a repeated browser POST from adding stock twice.
    request_id = models.UUIDField(unique=True)
    stock = models.ForeignKey(Stock, on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField()
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
    payload = models.JSONField(default=dict)
    transferred_at = models.DateTimeField(null=True,blank=True)
    arrival_stock = models.ForeignKey(Stock,on_delete=models.PROTECT,null=True,blank=True,related_name='arrived_receipts')
    cancelled_at = models.DateTimeField(null=True,blank=True)
    cancelled_by = models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,null=True,blank=True,related_name='cancelled_receipts')
    cancellation_reason = models.TextField(blank=True)
    class Meta:
        constraints = [models.CheckConstraint(condition=Q(quantity__gt=0), name='receipt_positive')]


class ErpSnapshot(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    label = models.CharField(max_length=150)
    erp_as_of = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    digest = models.CharField(max_length=64)
    source_name = models.CharField(max_length=255)


class ReconciliationRow(models.Model):
    snapshot = models.ForeignKey(ErpSnapshot, on_delete=models.PROTECT, related_name='rows')
    warehouse = models.CharField(max_length=10)
    code = models.CharField(max_length=100)
    name = models.CharField(max_length=250, blank=True)
    erp_quantity = models.IntegerField(null=True)
    shipping_pending = models.IntegerField(null=True)
    received = models.PositiveIntegerField(default=0)
    expected = models.PositiveIntegerField(default=0)
    reserved = models.PositiveIntegerField(default=0)
    review_state = models.CharField(max_length=12,choices=[('open','미검토'),('explained','원인 확인'),('resolved','조치 완료')],default='open')
    reason = models.TextField(blank=True)
    action_note = models.TextField(blank=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,null=True,blank=True)
    updated_at = models.DateTimeField(null=True,blank=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['snapshot','warehouse','code'],name='unique_snapshot_code')]
    @property
    def managed(self): return self.received+self.expected
    @property
    def difference(self):
        return self.managed-self.erp_quantity if self.warehouse=='Y09' and self.erp_quantity is not None else None
    @property
    def needs_review(self): return self.warehouse=='Y09' and (self.erp_quantity is None or self.difference!=0)


class LegacyArchiveBatch(models.Model):
    digest = models.CharField(max_length=64,unique=True)
    source_name = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    counts = models.JSONField(default=dict)


class LegacyArchiveRecord(models.Model):
    batch = models.ForeignKey(LegacyArchiveBatch,on_delete=models.PROTECT,related_name='records')
    kind = models.CharField(max_length=20,choices=[('receipt','기존 입고 백업'),('picking','기존 피킹/출고 백업'),('log','기존 작업로그'),('movement','기존 AppSheet 이동')])
    source_row = models.PositiveIntegerField()
    code = models.CharField(max_length=100,blank=True)
    description = models.TextField(blank=True)
    payload = models.JSONField(default=dict)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['batch','kind','source_row'],name='unique_archive_source_row')]


class Substitution(models.Model):
    """One immutable requested-to-actual link per physical allocation."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    allocation = models.OneToOneField(Allocation, on_delete=models.PROTECT, related_name='substitution')
    requested_code = models.CharField('고객/회사 ERP 요청 코드', max_length=100)
    actual_code = models.CharField('실물 출고 코드', max_length=100)
    reason = models.CharField('대체 사유', max_length=1000)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='created_substitutions')
    created_at = models.DateTimeField(auto_now_add=True)
    dispatched_at = models.DateTimeField(null=True, blank=True)
    settled_at = models.DateTimeField(null=True, blank=True)
    erp_confirmed_at = models.DateTimeField(null=True, blank=True)
    erp_confirmed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name='confirmed_substitutions')
    erp_note = models.CharField(max_length=1000, blank=True)
    class Meta:
        constraints = [models.CheckConstraint(condition=~Q(requested_code=F('actual_code')), name='substitution_different_codes')]


class SubstitutionSettlement(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    substitution = models.ForeignKey(Substitution,on_delete=models.PROTECT,related_name='settlements')
    receipt = models.ForeignKey(Receipt,on_delete=models.PROTECT,related_name='substitution_settlements')
    source_stock = models.ForeignKey(Stock,on_delete=models.PROTECT,related_name='settlements_from')
    target_stock = models.ForeignKey(Stock,on_delete=models.PROTECT,related_name='settlements_to')
    quantity = models.PositiveIntegerField()
    location = models.CharField(max_length=150)
    note = models.CharField(max_length=1000)
    product_confirmed = models.BooleanField(default=False)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        constraints = [models.CheckConstraint(condition=Q(quantity__gt=0),name='positive_substitution_settlement')]
