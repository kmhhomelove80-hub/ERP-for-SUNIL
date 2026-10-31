from django.core.exceptions import ValidationError
from .locking import warehouse_transaction
from django.utils import timezone
from .models import Stock, Allocation, MovementRequest, AuditEvent
from .transaction_details import stock_details

MAX_QUANTITY=2147483647

def require_quantity(quantity):
    if isinstance(quantity,bool) or not isinstance(quantity,int) or not 0<quantity<=MAX_QUANTITY:
        raise ValidationError('수량은 허용 범위 내의 양의 정수여야 합니다.')

def audit(actor, action, reference, **detail):
    AuditEvent.objects.create(actor=actor, action=action, reference=str(reference), detail=detail)

@warehouse_transaction
def reserve(*, stock_id, project, quantity, actor):
    stock = Stock.objects.select_for_update().get(pk=stock_id)
    if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity <= 0 or quantity > stock.available or not project.strip():
        raise ValidationError('프로젝트와 가용량 이내의 양수 수량이 필요합니다.')
    stock.reserved += quantity
    stock.save(update_fields=['reserved'])
    allocation = Allocation.objects.create(stock=stock, project=project.strip(), quantity=quantity,assigned_details=stock_details(stock))
    audit(actor, 'reserve', allocation.pk, quantity=quantity, stock=stock.pk, code=stock.item.code, project=project.strip())
    return allocation

@warehouse_transaction
def cancel_allocation(*, allocation_id, actor):
    a = Allocation.objects.select_for_update().get(pk=allocation_id)
    if a.state != 'reserved': raise ValidationError('예약 상태만 취소할 수 있습니다.')
    stock = Stock.objects.select_for_update().get(pk=a.stock_id)
    stock.reserved -= a.quantity
    stock.save(update_fields=['reserved'])
    a.state = 'cancelled'
    a.cancelled_at=timezone.now()
    a.completed_by=actor
    a.completed_details=stock_details(stock)
    a.save(update_fields=['state','cancelled_at','completed_by','completed_details'])
    audit(actor, 'cancel', a.pk, quantity=a.quantity,code=stock.item.code,project=a.project)

@warehouse_transaction
def release_selected(*, allocation_ids, shipped_at, actor):
    ids = set(allocation_ids)
    if not ids or shipped_at is None or timezone.is_naive(shipped_at) or shipped_at > timezone.now():
        raise ValidationError('출고 대상과 시간대가 포함된 실제 출고 시각이 필요합니다.')
    allocations = list(Allocation.objects.select_for_update().filter(pk__in=ids).order_by('pk'))
    if len(allocations) != len(ids) or any(a.state != 'reserved' for a in allocations):
        raise ValidationError('모든 대상이 유효한 예약이어야 합니다.')
    stocks = {s.pk:s for s in Stock.objects.select_for_update().filter(pk__in=[a.stock_id for a in allocations]).order_by('pk')}
    from .models import Substitution
    if Substitution.objects.filter(allocation_id__in=ids).exists():
        raise ValidationError('대체 배정은 일반 Release할 수 없습니다. 피킹의 대체 출고(빌림)를 사용하세요.')
    if any(s.status != Stock.Status.RECEIVED for s in stocks.values()):
        raise ValidationError('입고 완료 후 실제 출고한 물량만 Release할 수 있습니다.')
    for a in allocations:
        stock = stocks[a.stock_id]
        stock.quantity -= a.quantity
        stock.reserved -= a.quantity
        stock.save(update_fields=['quantity', 'reserved'])
        a.state, a.shipped_at, a.released_at = 'released', shipped_at, timezone.now()
        a.completed_by=actor
        a.completed_details=stock_details(stock)
        a.save(update_fields=['state','shipped_at','released_at','completed_by','completed_details'])
        audit(actor, 'release', a.pk, quantity=a.quantity, shipped_at=shipped_at.isoformat(),code=stock.item.code,project=a.project)

@warehouse_transaction
def submit_movement(*, request_id, source_id, destination, quantity, actor):
    destination = destination.strip()
    previous = MovementRequest.objects.filter(pk=request_id).first()
    if previous:
        if (previous.requested_by_id, previous.source_id, previous.destination, previous.quantity) != (actor.pk,source_id,destination,quantity):
            raise ValidationError('이미 사용한 요청번호입니다.')
        return previous
    source = Stock.objects.select_for_update().select_related('item').get(pk=source_id)
    if not isinstance(quantity,int) or isinstance(quantity,bool) or quantity <= 0 or quantity > source.available:
        raise ValidationError('요청 수량은 가용 잔량 이내의 양수여야 합니다.')
    if source.status != Stock.Status.RECEIVED or not destination or len(destination)>150 or destination.casefold()==source.location.casefold():
        raise ValidationError('입고 완료 재고와 서로 다른 출발·도착 위치가 필요합니다.')
    snapshot = dict(code=source.item.code,location=source.location,warehouse=source.warehouse)
    request = MovementRequest.objects.create(id=request_id,source=source,destination=destination,quantity=quantity,requested_by=actor,snapshot=snapshot)
    audit(actor,'movement_requested',request.pk,**snapshot,destination=destination,quantity=quantity)
    return request


@warehouse_transaction
def process_movement(*, request_id, actor, rejection_reason=''):
    request = MovementRequest.objects.select_for_update().get(pk=request_id)
    if request.state != 'pending': return request
    source = Stock.objects.select_for_update().select_related('item').get(pk=request.source_id)
    destination = request.destination.strip()
    before = dict(source_quantity=source.quantity,source_reserved=source.reserved)
    snapshot_changed = bool(request.snapshot) and (request.snapshot.get('code'), request.snapshot.get('location'), request.snapshot.get('warehouse')) != (source.item.code,source.location,source.warehouse)
    if rejection_reason.strip():
        request.state, request.reason = 'rejected', rejection_reason.strip()
    elif snapshot_changed or source.status != Stock.Status.RECEIVED or not destination or destination.casefold() == source.location.casefold() or request.quantity <= 0 or request.quantity > source.available:
        request.state, request.reason = 'rejected', '입고 상태·위치 또는 가용 잔량을 확인하세요.'
    else:
        target, _ = Stock.objects.get_or_create(item=source.item, warehouse=source.warehouse, location=destination, status=Stock.Status.RECEIVED,defaults={'legacy_name':source.legacy_name,'legacy_specification':source.legacy_specification})
        target = Stock.objects.select_for_update().get(pk=target.pk)
        before['target_quantity'] = target.quantity
        if target.quantity+request.quantity>MAX_QUANTITY:
            request.state,request.reason='rejected','도착 위치의 합산 재고가 허용 범위를 초과합니다.'
        else:
            source.quantity -= request.quantity
            target.quantity += request.quantity
            source.save(update_fields=['quantity'])
            target.save(update_fields=['quantity'])
            request.state = 'completed'
    request.processed_at = timezone.now()
    request.processed_by = actor
    request.save(update_fields=['state','reason','processed_at','processed_by'])
    audit(actor, 'movement', request.pk, state=request.state, quantity=request.quantity,code=source.item.code,source_location=source.location,destination=destination,reason=request.reason,before=before,source_after=source.quantity)
    return request

@warehouse_transaction
def receive_stock(*, item_id, location, quantity, status, expected_date, sender, request_id, actor):
    from .models import Receipt
    require_quantity(quantity)
    location, sender = location.strip(), sender.strip()
    payload = dict(item_id=item_id, location=location, quantity=quantity, status=status, expected_date=expected_date.isoformat() if expected_date else None, sender=sender)
    previous = Receipt.objects.select_related('stock').filter(request_id=request_id).first()
    if previous:
        if previous.actor_id != actor.pk or previous.payload != payload:
            raise ValidationError('이미 사용한 요청번호입니다.')
        return previous
    if not location or len(location)>150 or len(sender)>100 or status not in Stock.Status.values:
        raise ValidationError('위치·상태·양수 수량을 확인하세요.')
    if status == Stock.Status.EXPECTED and (not expected_date or not sender):
        raise ValidationError('예정 재고에는 예정일과 담당자가 필요합니다.')
    stock, _ = Stock.objects.get_or_create(item_id=item_id, warehouse='Y09', location=location, status=status)
    stock = Stock.objects.select_for_update().get(pk=stock.pk)
    if status == Stock.Status.EXPECTED and stock.quantity and (stock.expected_date != expected_date or stock.sender != sender):
        raise ValidationError('다른 예정일/담당자의 재고입니다. 별도 임시 식별자를 사용하세요.')
    if stock.quantity+quantity>MAX_QUANTITY:raise ValidationError('합산 재고가 허용 범위를 초과합니다.')
    receipt = Receipt.objects.create(request_id=request_id,stock=stock,quantity=quantity,actor=actor,payload=payload)
    stock.quantity += quantity
    stock.expected_date = expected_date if status == Stock.Status.EXPECTED else None
    stock.sender = sender if status == Stock.Status.EXPECTED else ''
    stock.save()
    audit(actor,'receipt',receipt.pk,stock=stock.pk,quantity=quantity,status=status,code=stock.item.code,location=stock.location)
    return receipt

@warehouse_transaction
def confirm_arrival(*, stock_id, location, actor):
    """Convert an entire expected bucket; keep active reservations attached."""
    source = Stock.objects.select_for_update().get(pk=stock_id)
    location = location.strip()
    if source.status != Stock.Status.EXPECTED or source.quantity <= 0 or not location:
        raise ValidationError('입고 예정 재고와 실제 보관 위치를 확인하세요.')
    target, _ = Stock.objects.get_or_create(item=source.item, warehouse=source.warehouse, location=location, status=Stock.Status.RECEIVED,defaults={'legacy_name':source.legacy_name,'legacy_specification':source.legacy_specification})
    target = Stock.objects.select_for_update().get(pk=target.pk)
    qty, reserved = source.quantity, source.reserved
    if target.quantity+qty>MAX_QUANTITY:raise ValidationError('합산 재고가 허용 범위를 초과합니다.')
    source.quantity, source.reserved = 0, 0
    source.save(update_fields=['quantity','reserved'])
    target.quantity += qty
    target.reserved += reserved
    target.save(update_fields=['quantity','reserved'])
    Allocation.objects.filter(stock=source,state='reserved').update(stock=target)
    from .models import Receipt
    Receipt.objects.filter(stock=source,cancelled_at__isnull=True,transferred_at__isnull=True).update(transferred_at=timezone.now(),arrival_stock=target)
    audit(actor,'arrival',source.pk,target=target.pk,quantity=qty,reserved=reserved,code=source.item.code,source_location=source.location,destination=location)
    return target

@warehouse_transaction
def cancel_selected(*, allocation_ids, actor):
    ids = set(allocation_ids)
    if not ids: raise ValidationError('취소 대상을 선택하세요.')
    rows = list(Allocation.objects.filter(pk__in=ids))
    if len(rows) != len(ids) or any(a.state != 'reserved' for a in rows):
        raise ValidationError('모든 대상이 유효한 예약이어야 합니다.')
    for allocation in sorted(rows,key=lambda a: a.pk):
        cancel_allocation(allocation_id=allocation.pk,actor=actor)

@warehouse_transaction
def cancel_receipt(*, receipt_id, reason, actor):
    from .models import Receipt
    receipt=Receipt.objects.select_for_update().get(pk=receipt_id)
    if receipt.cancelled_at:return receipt
    reason=reason.strip()
    if not reason:raise ValidationError('입고 취소 사유를 입력하세요.')
    if receipt.substitution_settlements.exists():raise ValidationError('상환에 사용한 입고 원거래는 취소할 수 없습니다.')
    if receipt.transferred_at:raise ValidationError('도착 확인으로 위치가 전환된 예정 입고는 원거래 취소할 수 없습니다.')
    stock=Stock.objects.select_for_update().select_related('item').get(pk=receipt.stock_id)
    if stock.available<receipt.quantity:raise ValidationError('예약·출고·이동 등으로 취소 가능한 가용 수량이 부족합니다.')
    before=stock.quantity;stock.quantity-=receipt.quantity;stock.save(update_fields=['quantity'])
    receipt.cancelled_at=timezone.now();receipt.cancelled_by=actor;receipt.cancellation_reason=reason
    receipt.save(update_fields=['cancelled_at','cancelled_by','cancellation_reason'])
    audit(actor,'receipt_cancel',receipt.pk,code=stock.item.code,quantity=receipt.quantity,location=stock.location,reason=reason,before=before,after=stock.quantity)
    return receipt
