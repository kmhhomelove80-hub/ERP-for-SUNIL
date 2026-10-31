"""Audited 1:1 substitutions: requested demand and physical stock stay separate."""
from collections import defaultdict
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.utils import timezone
from .locking import warehouse_transaction
from .models import Substitution, Allocation, Stock, PickingOrder
from .services import require_quantity, audit
from .transaction_details import stock_details


def requested_code(allocation):
    try: return allocation.substitution.requested_code
    except Substitution.DoesNotExist: return allocation.display_details['code']


def demand_balance(order):
    demands={r['code']:r['quantity'] for r in order.payload.get('rows',[])}
    quantities=defaultdict(int)
    for allocation in order.allocations.select_related('stock__item','substitution').exclude(state='cancelled'):
        quantities[requested_code(allocation)]+=allocation.quantity
    return [dict(code=code,requested=quantity,allocated=quantities[code],shortage=max(quantity-quantities[code],0)) for code,quantity in demands.items()]


@warehouse_transaction
def reserve_substitution(*, request_id, order_id, requested, stock_id, quantity, reason, actor):
    if not actor.is_active or not actor.is_staff: raise ValidationError('관리자만 대체 출고를 배정할 수 있습니다.')
    require_quantity(quantity)
    reason=reason.strip();requested=requested.strip()
    if not reason or len(reason)>1000: raise ValidationError('대체 사유를 1~1,000자로 입력하세요.')
    order=PickingOrder.objects.select_for_update().get(pk=order_id)
    stock=Stock.objects.select_for_update().select_related('item').get(pk=stock_id)
    previous=Substitution.objects.select_related('allocation').filter(pk=request_id).first()
    if previous:
        a=previous.allocation
        if (a.order_id,a.stock_id,a.quantity,previous.requested_code,previous.reason,previous.created_by_id)!=(order.pk,stock.pk,quantity,requested,reason,actor.pk):
            raise ValidationError('이미 사용한 대체 요청번호입니다.')
        return previous
    balances={r['code']:r for r in demand_balance(order)}
    if requested not in balances or quantity>balances[requested]['shortage']:
        raise ValidationError('요청 코드의 현재 미배정 수량 이내로 배정하세요. 취소/다른 배정을 확인하세요.')
    if stock.warehouse!='Y09' or stock.item.code==requested or quantity>stock.available:
        raise ValidationError('다른 코드의 부산 재고와 가용량을 확인하세요.')
    stock.reserved+=quantity;stock.save(update_fields=['reserved'])
    allocation=Allocation.objects.create(stock=stock,order=order,project=order.project,quantity=quantity,assigned_details=stock_details(stock))
    record=Substitution.objects.create(id=request_id,allocation=allocation,requested_code=requested,actual_code=stock.item.code,reason=reason,created_by=actor)
    audit(actor,'substitution_reserved',record.pk,allocation=str(allocation.pk),order=str(order.pk),project=order.project,requested_code=requested,actual_code=stock.item.code,code=stock.item.code,location=stock.location,quantity=quantity,reason=reason)
    return record


@warehouse_transaction
def confirm_company_erp(*, substitution_id, confirmed, note, actor):
    if not actor.is_active or not actor.is_staff: raise ValidationError('관리자만 확인할 수 있습니다.')
    record=Substitution.objects.select_for_update().select_related('allocation').get(pk=substitution_id)
    note=note.strip()
    if record.allocation.state!='settled':raise ValidationError('전량 상환 완료 후 회사 ERP 정산을 확인하세요.')
    if not note or len(note)>1000:raise ValidationError('확인 또는 확인 취소 근거를 1~1,000자로 남기세요.')
    if bool(record.erp_confirmed_at)==confirmed and record.erp_note==note:return record
    before=dict(confirmed=bool(record.erp_confirmed_at),note=record.erp_note)
    record.erp_confirmed_at=timezone.now() if confirmed else None
    record.erp_confirmed_by=actor if confirmed else None
    record.erp_note=note
    record.save(update_fields=['erp_confirmed_at','erp_confirmed_by','erp_note'])
    audit(actor,'substitution_erp',record.pk,allocation=str(record.allocation_id),project=record.allocation.project,requested_code=record.requested_code,actual_code=record.actual_code,confirmed=confirmed,note=note,before=before)
    return record


def related_for_snapshot(snapshot, codes=None):
    rows=Substitution.objects.select_related('allocation__stock__item','allocation__order','created_by','erp_confirmed_by').prefetch_related('settlements').filter(
        dispatched_at__lte=snapshot.created_at,allocation__shipped_at__lte=snapshot.erp_as_of)
    if codes is not None: rows=rows.filter(Q(requested_code__in=codes)|Q(actual_code__in=codes))
    return rows


def snapshot_summaries(snapshot,codes):
    result=defaultdict(lambda:dict(count=0,quantity=0,outstanding=0))
    for record in related_for_snapshot(snapshot,codes):
        for code in [record.requested_code,record.actual_code]:
            result[code]['count']+=1;result[code]['quantity']+=record.allocation.quantity
            result[code]['outstanding']+=record.allocation.quantity-repaid_quantity(record,min(snapshot.created_at,snapshot.erp_as_of))
    return result


def repaid_quantity(record, cutoff=None):
    from django.db.models import Sum
    cached=getattr(record,'_prefetched_objects_cache',{}).get('settlements')
    if cached is not None:return sum(s.quantity for s in cached if cutoff is None or s.created_at<=cutoff)
    rows=record.settlements.all()
    if cutoff is not None:rows=rows.filter(created_at__lte=cutoff)
    return rows.aggregate(q=Sum('quantity'))['q'] or 0


@warehouse_transaction
def dispatch_substitution(*, substitution_id, shipped_at, product_confirmed, actor):
    if not actor.is_active or not actor.is_staff:raise ValidationError('관리자만 처리할 수 있습니다.')
    if not product_confirmed:raise ValidationError('동일 제품 또는 필요한 수정 완료를 확인하세요.')
    if shipped_at is None or timezone.is_naive(shipped_at) or shipped_at>timezone.now():raise ValidationError('실제 출고 시각을 확인하세요.')
    record=Substitution.objects.select_for_update().select_related('allocation').get(pk=substitution_id)
    a=Allocation.objects.select_for_update().get(pk=record.allocation_id)
    if record.dispatched_at:
        if a.shipped_at!=shipped_at:raise ValidationError('이미 처리한 대체 출고입니다. 기존 시각을 확인하세요.')
        return record
    if a.state!='reserved':raise ValidationError('예약 중인 대체 배정만 출고할 수 있습니다.')
    stock=Stock.objects.select_for_update().select_related('item').get(pk=a.stock_id)
    if stock.status!='received' or stock.quantity<a.quantity or stock.reserved<a.quantity:
        raise ValidationError('입고 완료와 실물/예약 수량을 확인하세요.')
    a.completed_details=stock_details(stock)
    stock.quantity-=a.quantity;stock.reserved-=a.quantity;stock.save(update_fields=['quantity','reserved'])
    a.state='substituted';a.shipped_at=shipped_at;a.completed_by=actor
    a.save(update_fields=['state','shipped_at','completed_by','completed_details'])
    record.dispatched_at=timezone.now();record.save(update_fields=['dispatched_at'])
    audit(actor,'substitution_dispatch',record.pk,allocation=str(a.pk),project=a.project,requested_code=record.requested_code,actual_code=record.actual_code,code=record.actual_code,quantity=a.quantity,location=stock.location,shipped_at=shipped_at.isoformat(),product_confirmed=True)
    return record


@warehouse_transaction
def settle_substitution(*, request_id, substitution_id, receipt_id, quantity, note, product_confirmed, actor):
    from .models import Receipt, SubstitutionSettlement
    from django.db.models import Sum
    if not actor.is_active or not actor.is_staff:raise ValidationError('관리자만 상환할 수 있습니다.')
    require_quantity(quantity);note=note.strip()
    if not product_confirmed or not note or len(note)>1000:raise ValidationError('동일 제품/수정 완료 확인과 상환 근거를 입력하세요.')
    previous=SubstitutionSettlement.objects.filter(pk=request_id).first()
    if previous:
        if (previous.substitution_id,previous.receipt_id,previous.quantity,previous.note,previous.actor_id)!=(substitution_id,receipt_id,quantity,note,actor.pk):
            raise ValidationError('이미 사용한 상환 요청번호입니다.')
        return previous
    record=Substitution.objects.select_for_update().select_related('allocation__stock__item').get(pk=substitution_id)
    a=Allocation.objects.select_for_update().get(pk=record.allocation_id)
    outstanding=a.quantity-repaid_quantity(record)
    if a.state!='substituted' or not record.dispatched_at or quantity>outstanding:
        raise ValidationError('미상환 대체 출고의 잔량 이내로 상환하세요.')
    receipt=Receipt.objects.select_for_update().get(pk=receipt_id)
    source_id=receipt.arrival_stock_id or receipt.stock_id
    source=Stock.objects.select_for_update().select_related('item').get(pk=source_id)
    used=receipt.substitution_settlements.aggregate(q=Sum('quantity'))['q'] or 0
    if receipt.cancelled_at or source.status!='received' or source.warehouse!='Y09' or source.item.code!=record.requested_code:
        raise ValidationError('요청 코드로 실제 입고 완료된 유효한 입고 건을 선택하세요.')
    if (receipt.transferred_at if receipt.arrival_stock_id else receipt.created_at)<a.shipped_at:
        raise ValidationError('대체 출고 후 등록한 입고 건으로 상환하세요.')
    if quantity>receipt.quantity-used or quantity>source.available:
        raise ValidationError('입고 건 미사용 수량과 현재 가용 재고가 부족합니다.')
    target,_=Stock.objects.get_or_create(item_id=a.stock.item_id,warehouse='Y09',status='received',location=source.location,
                                       defaults={'legacy_name':a.stock.legacy_name,'legacy_specification':a.stock.legacy_specification})
    target=Stock.objects.select_for_update().get(pk=target.pk)
    if target.quantity+quantity>2147483647:raise ValidationError('상환 후 재고 수량이 허용 범위를 초과합니다.')
    source.quantity-=quantity;target.quantity+=quantity
    source.save(update_fields=['quantity']);target.save(update_fields=['quantity'])
    settlement=SubstitutionSettlement.objects.create(id=request_id,substitution=record,receipt=receipt,source_stock=source,target_stock=target,quantity=quantity,location=source.location,note=note,product_confirmed=True,actor=actor)
    if quantity==outstanding:
        a.state='settled';a.save(update_fields=['state'])
        record.settled_at=timezone.now();record.save(update_fields=['settled_at'])
    audit(actor,'substitution_settlement',record.pk,settlement=str(settlement.pk),receipt=str(receipt.pk),allocation=str(a.pk),project=a.project,requested_code=record.requested_code,actual_code=record.actual_code,code=record.actual_code,quantity=quantity,location=source.location,note=note,remaining=outstanding-quantity)
    return settlement


@warehouse_transaction
def cancel_substitution(*,substitution_id,note,actor):
    from .services import cancel_selected
    if not actor.is_active or not actor.is_staff:raise ValidationError('관리자만 취소할 수 있습니다.')
    record=Substitution.objects.select_for_update().select_related('allocation').get(pk=substitution_id)
    if record.allocation.state!='reserved' or record.dispatched_at:raise ValidationError('빌림 출고 전 예약만 취소할 수 있습니다.')
    if not note.strip() or len(note)>1000:raise ValidationError('취소 사유를 남기세요.')
    cancel_selected(allocation_ids=[record.allocation_id],actor=actor)
    audit(actor,'substitution_cancel',record.pk,allocation=str(record.allocation_id),project=record.allocation.project,requested_code=record.requested_code,actual_code=record.actual_code,note=note.strip())
