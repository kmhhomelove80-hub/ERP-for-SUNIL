from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Q, Sum
from django.shortcuts import render, redirect, get_object_or_404
from .models import Stock
from .forms import ReceiptForm, ArrivalForm
from .services import receive_stock, confirm_arrival

@login_required
def stock_list(request):
    query = request.GET.get('q','').strip()
    status = request.GET.get('status','')
    stocks = Stock.objects.select_related('item').filter(quantity__gt=0).order_by('item__code','location','pk')
    if query:
        stocks = stocks.filter(Q(item__code__icontains=query)|Q(item__name__icontains=query)|Q(item__specification__icontains=query)|Q(location__icontains=query)|Q(legacy_name__icontains=query)|Q(legacy_specification__icontains=query))
    if status in Stock.Status.values: stocks = stocks.filter(status=status)
    totals = stocks.aggregate(quantity=Sum('quantity'),reserved=Sum('reserved'))
    page=Paginator(stocks,50).get_page(request.GET.get('page'))
    from .reconciliation import latest_reference
    reference,erp_snapshot=latest_reference('Y09',[s.item.code for s in page.object_list])
    for stock in page.object_list:stock.erp_pending=reference.get(stock.item.code,{}).get('shipping_pending')
    return render(request,'inventory/stock_list.html',{'page_obj':page, 'query':query,'status':status,'totals':totals,'statuses':Stock.Status.choices,'erp_snapshot':erp_snapshot})

@user_passes_test(lambda user: user.is_active and user.is_staff)
def receipt_create(request):
    form = ReceiptForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        d = form.cleaned_data
        try:
            receive_stock(item_id=d['item'].pk,location=d['location'],quantity=d['quantity'],status=d['status'],expected_date=d['expected_date'],sender=d['sender'],request_id=d['request_id'],actor=request.user)
        except ValidationError as error: form.add_error(None,error)
        else:
            messages.success(request,'입고 등록이 완료되었습니다.')
            return redirect('stock-list')
    return render(request,'inventory/form.html',{'form':form,'title':'입고 / 예정 재고 등록','description':'새 물량을 등록합니다. 이미 등록한 예정 물량이 도착했다면 재고 목록의 입고 확인을 사용하세요.'})

@user_passes_test(lambda user: user.is_active and user.is_staff)
def arrival_confirm(request, pk):
    stock = get_object_or_404(Stock.objects.select_related('item'),pk=pk,status='expected',quantity__gt=0)
    form = ArrivalForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        try: confirm_arrival(stock_id=stock.pk,location=form.cleaned_data['location'],actor=request.user)
        except ValidationError as error: form.add_error(None,error)
        else:
            messages.success(request,'실제 입고 위치를 반영했습니다. 현재고 총량과 예약량은 유지됩니다.')
            return redirect('stock-list')
    return render(request,'inventory/form.html',{'form':form,'title':'예정 재고 도착 확인','description':f'{stock.item.code} · {stock.location} · 전량 {stock.quantity}개 / 예약 {stock.reserved}개를 실제 위치로 전환합니다.'})

@user_passes_test(lambda user: user.is_active and user.is_staff)
def picking_create(request):
    from django.core import signing
    from .forms import PickingInputForm, PickingConfirmForm
    from .picking import plan_picking, confirm_picking
    input_form = PickingInputForm(request.POST if request.method == 'POST' and request.POST.get('action') == 'preview' else None)
    preview, confirm_form = None, None
    if request.method == 'POST' and request.POST.get('action') == 'preview' and input_form.is_valid():
        d = input_form.cleaned_data
        preview = plan_picking(d['pasted_rows'])
        payload = dict(actor=request.user.pk,request_id=str(d['request_id']),project=d['project'],rows=d['pasted_rows'],preview=preview)
        token = signing.dumps(payload,salt='picking-preview',compress=True)
        confirm_form = PickingConfirmForm(initial={'preview_token':token})
    elif request.method == 'POST' and request.POST.get('action') == 'confirm':
        confirm_form = PickingConfirmForm(request.POST)
        if confirm_form.is_valid():
            try:
                payload = signing.loads(confirm_form.cleaned_data['preview_token'],salt='picking-preview',max_age=1800)
                if payload['actor'] != request.user.pk: raise ValidationError('다른 사용자의 미리보기입니다.')
                input_form = PickingInputForm(initial={'request_id':payload['request_id'],'project':payload['project'],'pasted_rows':'\n'.join(f"{r['code']}\t{r['quantity']}" for r in payload['rows'])})
                from uuid import UUID
                order = confirm_picking(request_id=UUID(payload['request_id']),project=payload['project'],rows=payload['rows'],preview=payload['preview'],allow_shortage=confirm_form.cleaned_data['allow_shortage'],actor=request.user)
            except (signing.BadSignature,ValidationError) as error:
                messages.error(request,' / '.join(error.messages) if isinstance(error,ValidationError) else '미리보기가 만료되었거나 유효하지 않습니다. 다시 계산하세요.')
                confirm_form = None
            else:
                messages.success(request,'피킹 예약을 등록했습니다. 재고 차감은 실제 출고 후 Release에서 수행하세요.')
                return redirect('picking-detail',pk=order.pk)
    return render(request,'inventory/picking_create.html',{'input_form':input_form,'preview':preview,'confirm_form':confirm_form})

@login_required
def picking_detail(request, pk):
    from .models import PickingOrder
    order = get_object_or_404(PickingOrder,pk=pk)
    rows = order.allocations.select_related('stock__item').order_by('stock__item__code','stock__pk')
    return render(request,'inventory/picking_detail.html',{'order':order,'allocations':rows})

@user_passes_test(lambda user: user.is_active and user.is_staff)
def allocation_list(request):
    from .models import Allocation
    from .forms import AllocationActionForm
    from .services import release_selected, cancel_selected
    form = AllocationActionForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        data = form.cleaned_data
        ids = list(data['allocations'].values_list('pk',flat=True))
        try:
            if data['action'] == 'release': release_selected(allocation_ids=ids,shipped_at=data['shipped_at'],actor=request.user)
            else: cancel_selected(allocation_ids=ids,actor=request.user)
        except ValidationError as error: form.add_error(None,error)
        else:
            messages.success(request,f'{len(ids)}개 예약 행을 처리했습니다.')
            return redirect('allocation-list')
    rows = Allocation.objects.filter(state='reserved').select_related('stock__item','order').order_by('project','stock__item__code','pk')
    return render(request,'inventory/allocations.html',{'form':form,'allocations':rows,'selected_ids':request.POST.getlist('allocations')})

@login_required
def movement_create(request, pk):
    from .forms import MovementRequestForm
    from .services import submit_movement
    stock = get_object_or_404(Stock.objects.select_related('item'),pk=pk)
    form = MovementRequestForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        d = form.cleaned_data
        try: movement = submit_movement(request_id=d['request_id'],source_id=stock.pk,destination=d['destination'],quantity=d['quantity'],actor=request.user)
        except ValidationError as error: form.add_error(None,error)
        else:
            messages.success(request,'이동 요청을 등록했습니다. 관리자 처리 전까지 전산 재고는 변경되지 않습니다.')
            return redirect('movement-detail',pk=movement.pk)
    return render(request,'inventory/movement_create.html',{'stock':stock,'form':form})

@login_required
def movement_list(request):
    from .models import MovementRequest
    status = request.GET.get('status','pending')
    query = request.GET.get('q','').strip()
    rows = MovementRequest.objects.select_related('source__item','requested_by','processed_by').order_by('-requested_at','pk')
    if not request.user.is_staff: rows = rows.filter(requested_by=request.user)
    if status in ['pending','completed','rejected']: rows = rows.filter(state=status)
    else: status = ''
    if query: rows = rows.filter(Q(source__item__code__icontains=query)|Q(source__location__icontains=query)|Q(destination__icontains=query))
    return render(request,'inventory/movements.html',{'page_obj':Paginator(rows,30).get_page(request.GET.get('page')),'status':status,'query':query})

@login_required
def movement_detail(request, pk):
    from .models import MovementRequest
    from .forms import MovementProcessForm
    from .services import process_movement
    from django.core.exceptions import PermissionDenied
    movement = get_object_or_404(MovementRequest.objects.select_related('source__item','requested_by','processed_by'),pk=pk)
    if not request.user.is_staff and movement.requested_by_id != request.user.pk: raise PermissionDenied
    form = MovementProcessForm(request.POST or None) if request.user.is_staff and movement.state=='pending' else None
    if request.method == 'POST':
        if not request.user.is_staff: raise PermissionDenied
        if form is not None and form.is_valid():
            data = form.cleaned_data
            process_movement(request_id=movement.pk,actor=request.user,rejection_reason=data['reason'] if data['action']=='reject' else '')
            messages.success(request,'이동 요청을 처리했습니다.')
            return redirect('movement-detail',pk=movement.pk)
        elif movement.state!='pending':
            messages.info(request,'이미 처리한 요청입니다. 중복 반영하지 않았습니다.')
            return redirect('movement-detail',pk=movement.pk)
    return render(request,'inventory/movement_detail.html',{'movement':movement,'form':form})

@user_passes_test(lambda user: user.is_active and user.is_staff)
def history_list(request):
    from .models import AuditEvent
    from .forms import HistoryFilterForm
    form = HistoryFilterForm(request.GET)
    rows = AuditEvent.objects.select_related('actor').order_by('-created_at','-pk')
    if form.is_valid():
        d = form.cleaned_data
        if d['action']: rows = rows.filter(action=d['action'])
        if d['start']: rows = rows.filter(created_at__date__gte=d['start'])
        if d['end']: rows = rows.filter(created_at__date__lte=d['end'])
        if d['q']:
            rows = rows.filter(Q(reference__icontains=d['q'])|Q(actor__username__icontains=d['q'])|Q(detail__code__icontains=d['q'])|Q(detail__project__icontains=d['q'])|Q(detail__source_location__icontains=d['q'])|Q(detail__location__icontains=d['q'])|Q(detail__destination__icontains=d['q'])|Q(detail__reason__icontains=d['q']))
    else: rows = rows.none()
    action_labels = dict(form.fields['action'].choices)
    page = Paginator(rows,50).get_page(request.GET.get('page'))
    from .history import event_summary
    for event in page.object_list:
        event.summary = event_summary(event)
        event.action_label = action_labels.get(event.action,event.action)
    params = request.GET.copy();params.pop('page',None)
    return render(request,'inventory/history.html',{'page_obj':page,'form':form,'filter_query':params.urlencode()})

@user_passes_test(lambda user: user.is_active and user.is_staff)
def erp_template(request):
    from django.http import HttpResponse
    from .reconciliation import template_bytes
    response=HttpResponse(template_bytes(),content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition']='attachment; filename="ERP_stock_template.xlsx"'
    return response

@user_passes_test(lambda user: user.is_active and user.is_staff)
def erp_upload(request):
    from django.core import signing
    from .forms import ErpUploadForm,ErpConfirmForm
    from .reconciliation import inspect_erp_file,create_snapshot
    upload_form=ErpUploadForm(request.POST if request.method=='POST' and request.POST.get('action')=='preview' else None,request.FILES or None)
    preview,confirm_form=None,None
    if request.method=='POST' and request.POST.get('action')=='preview' and upload_form.is_valid():
        d=upload_form.cleaned_data
        try: plan=inspect_erp_file(d['file'],d['layout'])
        except ValidationError as error:upload_form.add_error('file',error)
        else:
            preview=[]
            for warehouse in ['Y09','Y02']:
                rows=[r for r in plan['rows'] if r['warehouse']==warehouse]
                if rows:preview.append(dict(warehouse=warehouse,rows=len(rows),quantity=sum(r['quantity'] for r in rows),pending=sum(r['shipping_pending'] for r in rows),negative=sum(r['quantity']<0 or r['shipping_pending']<0 for r in rows)))
            payload=dict(actor=request.user.pk,request_id=str(d['request_id']),label=d['label'],erp_as_of=d['erp_as_of'].isoformat(),plan=plan)
            token=signing.dumps(payload,salt='erp-preview',compress=True)
            confirm_form=ErpConfirmForm(initial={'preview_token':token})
    elif request.method=='POST' and request.POST.get('action')=='confirm':
        confirm_form=ErpConfirmForm(request.POST)
        if confirm_form.is_valid():
            from datetime import datetime
            from uuid import UUID
            try:
                payload=signing.loads(confirm_form.cleaned_data['preview_token'],salt='erp-preview',max_age=1800)
                if payload['actor']!=request.user.pk: raise ValidationError('다른 사용자의 미리보기입니다.')
                snapshot=create_snapshot(request_id=UUID(payload['request_id']),label=payload['label'],erp_as_of=datetime.fromisoformat(payload['erp_as_of']),plan=payload['plan'],actor=request.user)
            except (signing.BadSignature,ValidationError) as error:
                messages.error(request,' / '.join(error.messages) if isinstance(error,ValidationError) else '미리보기가 만료되었거나 유효하지 않습니다. 파일을 다시 확인하세요.')
                confirm_form=None
            else:
                messages.success(request,'ERP 대조 자료를 등록했습니다. 관리 재고는 변경하지 않았습니다.')
                return redirect('erp-detail',pk=snapshot.pk)
    return render(request,'inventory/erp_upload.html',{'form':upload_form,'preview':preview,'confirm_form':confirm_form})

@user_passes_test(lambda user: user.is_active and user.is_staff)
def erp_list(request):
    from .models import ErpSnapshot
    rows=ErpSnapshot.objects.select_related('actor').order_by('-created_at','-pk')
    return render(request,'inventory/erp_list.html',{'page_obj':Paginator(rows,30).get_page(request.GET.get('page'))})

@user_passes_test(lambda user: user.is_active and user.is_staff)
def erp_detail(request,pk):
    from .models import ErpSnapshot
    from django.db.models import F
    snapshot=get_object_or_404(ErpSnapshot,pk=pk)
    warehouses=list(snapshot.rows.values_list('warehouse',flat=True).distinct())
    warehouse=request.GET.get('warehouse','Y09' if 'Y09' in warehouses else 'Y02')
    if warehouse not in ['Y09','Y02']:warehouse='Y09'
    issues=request.GET.get('issues','1')=='1'
    query=request.GET.get('q','').strip()
    rows=snapshot.rows.filter(warehouse=warehouse).order_by('code')
    if query:rows=rows.filter(Q(code__icontains=query)|Q(name__icontains=query))
    if warehouse=='Y09' and issues:
        rows=rows.filter(Q(erp_quantity__isnull=True)|~Q(erp_quantity=F('received')+F('expected')))
    totals=snapshot.rows.filter(warehouse=warehouse).aggregate(erp=Sum('erp_quantity'),received=Sum('received'),expected=Sum('expected'),reserved=Sum('reserved'))
    return render(request,'inventory/erp_detail.html',{'snapshot':snapshot,'page_obj':Paginator(rows,50).get_page(request.GET.get('page')),'warehouse':warehouse,'issues':issues,'query':query,'totals':totals})

@user_passes_test(lambda user: user.is_active and user.is_staff)
def erp_review(request,pk):
    from .models import ReconciliationRow
    from .forms import ReviewForm
    from .reconciliation import update_review
    row=get_object_or_404(ReconciliationRow.objects.select_related('snapshot'),pk=pk)
    form=ReviewForm(request.POST or None,initial={'state':row.review_state,'reason':row.reason,'action_note':row.action_note})
    if request.method=='POST' and form.is_valid():
        d=form.cleaned_data
        try:update_review(row_id=row.pk,state=d['state'],reason=d['reason'],action_note=d['action_note'],actor=request.user)
        except ValidationError as error:form.add_error(None,error)
        else:
            messages.success(request,'차이 원인과 조치 내용을 기록했습니다. 수량은 변경하지 않았습니다.')
            return redirect('erp-detail',pk=row.snapshot_id)
    return render(request,'inventory/form.html',{'form':form,'title':f'{row.code} 차이 분석','description':f'ERP {row.erp_quantity if row.erp_quantity is not None else "누락"} / 관리 재고 {row.managed}. 원래 대조 수량은 보존되며, 조치 완료는 이 차이에 대한 업무 처리 상태입니다.'})

@user_passes_test(lambda user:user.is_active and user.is_staff)
def archive_list(request):
    from .models import LegacyArchiveRecord
    rows=LegacyArchiveRecord.objects.select_related('batch').order_by('-batch_id','kind','-source_row')
    kind=request.GET.get('kind','')
    query=request.GET.get('q','').strip()
    if kind in ['receipt','picking','log','movement']:rows=rows.filter(kind=kind)
    if query:rows=rows.filter(Q(code__icontains=query)|Q(description__icontains=query)|Q(payload__seq__icontains=query)|Q(payload__username__icontains=query)|Q(payload__requested_by__icontains=query))
    page=Paginator(rows,50).get_page(request.GET.get('page'))
    import json
    for record in page.object_list:record.original_display=json.dumps(record.payload,ensure_ascii=False,indent=2)
    return render(request,'inventory/archives.html',{'page_obj':page,'kind':kind,'query':query,'kinds':LegacyArchiveRecord._meta.get_field('kind').choices})

@user_passes_test(lambda user:user.is_active and user.is_staff)
def receipt_list(request):
    from .models import Receipt
    query=request.GET.get('q','').strip()
    rows=Receipt.objects.select_related('stock__item','actor','cancelled_by').order_by('-created_at','pk')
    if query:rows=rows.filter(Q(stock__item__code__icontains=query)|Q(stock__location__icontains=query))
    return render(request,'inventory/receipts.html',{'page_obj':Paginator(rows,50).get_page(request.GET.get('page')),'query':query})

@user_passes_test(lambda user:user.is_active and user.is_staff)
def receipt_cancel(request,pk):
    from .models import Receipt
    from .forms import ReceiptCancelForm
    from .services import cancel_receipt
    receipt=get_object_or_404(Receipt.objects.select_related('stock__item'),pk=pk)
    form=ReceiptCancelForm(request.POST or None)
    if request.method=='POST' and form.is_valid():
        try:cancel_receipt(receipt_id=receipt.pk,reason=form.cleaned_data['reason'],actor=request.user)
        except ValidationError as error:form.add_error(None,error)
        else:
            messages.success(request,'입고 원거래를 취소했습니다. 원거래와 취소 이력은 보존됩니다.')
            return redirect('receipt-list')
    return render(request,'inventory/form.html',{'form':form,'title':'입고 원거래 취소','description':f'{receipt.stock.item.code} / {receipt.quantity}개 전량 취소. 현재 가용량 {receipt.stock.available}. 예약된 수량은 취소할 수 없으며, 예정→도착 전환 후에는 원거래 취소를 차단합니다.'})
