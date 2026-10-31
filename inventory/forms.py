import uuid
from django import forms
from .models import Item, Stock, Receipt

class ReceiptForm(forms.Form):
    request_id = forms.UUIDField(widget=forms.HiddenInput, initial=uuid.uuid4)
    item = forms.ModelChoiceField(label='품목', queryset=Item.objects.order_by('code'))
    status = forms.ChoiceField(label='입고 구분', choices=Stock.Status.choices)
    quantity = forms.IntegerField(label='수량', min_value=1, max_value=2147483647)
    location = forms.CharField(label='실제 위치 / 예정 재고 식별자', max_length=150)
    expected_date = forms.DateField(label='예정일', required=False, widget=forms.DateInput(attrs={'type':'date'}))
    sender = forms.CharField(label='발송 담당자',max_length=100,required=False)
    def clean(self):
        data = super().clean()
        if data.get('status') == Stock.Status.EXPECTED:
            for key in ['expected_date','sender']:
                if not data.get(key): self.add_error(key,'예정 재고에는 필수입니다.')
        return data

class ArrivalForm(forms.Form):
    location = forms.CharField(label='실제 입고 위치', max_length=150)

class PickingInputForm(forms.Form):
    request_id = forms.UUIDField(widget=forms.HiddenInput, initial=uuid.uuid4)
    project = forms.CharField(label='프로젝트 / 호선', max_length=250)
    pasted_rows = forms.CharField(required=False,label='수량 · ERP 코드 붙여넣기', max_length=100000, widget=forms.Textarea(attrs={'rows':12,'placeholder':'10\tERP001\n5\tERP002'}),help_text='수량 → ERP 코드 순서로 엑셀의 두 열을 복사하세요. 헤더 없이 최대 500행, 중복 품번은 합산합니다.')
    def clean_pasted_rows(self):
        from .picking import parse_quantity_code
        text = self.cleaned_data['pasted_rows']
        return parse_quantity_code(text) if text.strip() else []
    customer_rows = forms.CharField(label='고객 자료 6열 붙여넣기', required=False, max_length=300000, widget=forms.Textarea(attrs={'rows':8}), help_text='POR번호·호선·블록·품명·수량·ERP CODE. 기존 두 열 대신 사용하면 고객 양식도 인쇄할 수 있습니다.')
    customer_file = forms.FileField(label='또는 고객 자료 Excel 파일 (.xlsx / .xlsm)', required=False)
    def clean(self):
        data = super().clean()
        from .customer_picking import parse_customer
        try:
            customer = parse_customer(pasted=data.get('customer_rows',''), upload=data.get('customer_file')) if data.get('customer_rows') or data.get('customer_file') else []
            if customer and data.get('pasted_rows'):
                raise forms.ValidationError('두 열 입력과 고객 자료 입력 중 하나만 사용하세요.')
            if not customer and not data.get('pasted_rows'):
                raise forms.ValidationError('피킹 자료를 입력하세요.')
            if customer:
                from .picking import parse_paste
                data['pasted_rows'] = parse_paste('\n'.join(f"{r['code']}\t{r['quantity']}" for r in customer))
            data['customer_details'] = customer
        except forms.ValidationError as error:
            self.add_error(None,error)
        return data

class PickingConfirmForm(forms.Form):
    preview_token = forms.CharField(widget=forms.HiddenInput,max_length=500000)
    allow_shortage = forms.BooleanField(label='부족 수량을 확인했으며, 확보 가능한 수량만 예약합니다.', required=False)

class AllocationActionForm(forms.Form):
    from .models import Allocation
    allocations = forms.ModelMultipleChoiceField(label='대상 행',required=False,queryset=Allocation.objects.filter(state='reserved'),widget=forms.CheckboxSelectMultiple)
    action = forms.ChoiceField(label='처리',choices=[('release','실제 출고 완료 → Release'),('cancel','예약 취소')])
    shipped_at = forms.DateTimeField(label='실제 출고 시각 (한국 시간)',required=False,widget=forms.DateTimeInput(attrs={'type':'datetime-local'}))
    confirmed = forms.BooleanField(label='선택한 행과 수량을 확인했으며, 각 행의 전량을 처리합니다.')
    def clean(self):
        data = super().clean()
        if data.get('action') == 'release' and not data.get('shipped_at'):
            self.add_error('shipped_at','Release에는 실제 출고 시각이 필요합니다.')
        return data

class MovementRequestForm(forms.Form):
    request_id = forms.UUIDField(widget=forms.HiddenInput,initial=uuid.uuid4)
    destination = forms.CharField(label='이동할 위치',max_length=150,widget=forms.TextInput(attrs={'autocomplete':'off','autocapitalize':'characters'}))
    quantity = forms.IntegerField(label='요청 수량',min_value=1,max_value=2147483647,widget=forms.NumberInput(attrs={'inputmode':'numeric','pattern':'[0-9]*'}))
    confirmed = forms.BooleanField(label='품목·출발 위치·도착 위치·수량을 확인했습니다.')

class MovementProcessForm(forms.Form):
    action = forms.ChoiceField(label='처리',choices=[('process','가용량 확인 후 반영'),('reject','관리자 거부')])
    reason = forms.CharField(label='거부 사유',required=False,max_length=500,widget=forms.Textarea(attrs={'rows':3}))
    confirmed = forms.BooleanField(label='대상 요청과 수량을 확인했습니다.')
    def clean(self):
        data = super().clean()
        if data.get('action') == 'reject' and not data.get('reason'):
            self.add_error('reason','거부 사유를 입력하세요.')
        return data

class HistoryFilterForm(forms.Form):
    q = forms.CharField(label='검색',required=False,max_length=100)
    action = forms.ChoiceField(label='작업 종류',required=False,choices=[('','전체'),('initial_import','초기 이관'),('receipt','입고'),('receipt_batch','일괄 입고'),('substitution_reserved','대체 예약'),('substitution_dispatch','대체 출고(빌림)'),('substitution_settlement','입고분 상환'),('substitution_cancel','대체 예약 취소'),('substitution_erp','회사 ERP 정산 확인'),('receipt_cancel','입고 취소'),('arrival','도착 확인'),('reserve','예약'),('cancel','예약 취소'),('release','출고 확정'),('picking_confirm','피킹 등록'),('movement_requested','이동 요청'),('movement','이동 처리'),('erp_snapshot','ERP 대조 등록'),('reconciliation_review','차이 분석 기록'),('legacy_archive','과거 이력 보관 이관')])
    start = forms.DateField(label='시작일',required=False,widget=forms.DateInput(attrs={'type':'date'}))
    end = forms.DateField(label='종료일',required=False,widget=forms.DateInput(attrs={'type':'date'}))
    def clean(self):
        data = super().clean()
        if data.get('start') and data.get('end') and data['start']>data['end']:
            raise forms.ValidationError('종료일은 시작일 이후여야 합니다.')
        return data

class ErpUploadForm(forms.Form):
    from django.utils import timezone
    request_id = forms.UUIDField(widget=forms.HiddenInput,initial=uuid.uuid4)
    label = forms.CharField(label='자료 이름',max_length=150)
    erp_as_of = forms.DateTimeField(label='회사 ERP 자료 기준 시각 (한국 시간)',initial=timezone.now,widget=forms.DateTimeInput(format='%Y-%m-%dT%H:%M',attrs={'type':'datetime-local'}))
    layout = forms.ChoiceField(label='파일 형식',choices=[('export','ERP 직접 추출 y09.xls / y02.xls'),('standard','표준 ERP재고 양식'),('legacy','기존 SUNIL 파일의 재고현황 시트')])
    file = forms.FileField(label='기존/표준 ERP 재고 파일 (.xlsx / .xlsm)',required=False)
    y09_file = forms.FileField(label='Y09 부산 ERP 추출 파일 (.xls)',required=False)
    y02_file = forms.FileField(label='Y02 영주 ERP 추출 파일 (.xls)',required=False)
    confirmed_mapping = forms.BooleanField(label='기존 형식의 I:R은 Y02, W:AF는 Y09임을 확인했습니다.',required=False)
    def clean(self):
        data=super().clean()
        if data.get('layout')=='legacy' and not data.get('confirmed_mapping'):
            self.add_error('confirmed_mapping','기존 형식의 창고 배치를 확인하세요.')
        if data.get('layout')=='export' and not (data.get('y09_file') or data.get('y02_file')):
            self.add_error(None,'Y09 또는 Y02 추출 파일을 선택하세요. 두 파일을 함께 등록할 수 있습니다.')
        if data.get('layout')!='export' and not data.get('file'):
            self.add_error('file','파일을 선택하세요.')
        if any(data.get(k) and data[k].size>10*1024*1024 for k in ['file','y09_file','y02_file']):
            self.add_error('file','10MB 이하 파일을 사용하세요.')
        return data

class ErpConfirmForm(forms.Form):
    preview_token = forms.CharField(widget=forms.HiddenInput,max_length=2000000)
    confirmed = forms.BooleanField(label='자료 기준 시각과 창고별 수량을 확인했습니다.')

class ReviewForm(forms.Form):
    from .models import ReconciliationRow
    state = forms.ChoiceField(label='확인 상태',choices=ReconciliationRow._meta.get_field('review_state').choices)
    reason = forms.CharField(label='차이 원인',required=False,max_length=3000,widget=forms.Textarea(attrs={'rows':3}))
    action_note = forms.CharField(label='조치 내용',required=False,max_length=3000,widget=forms.Textarea(attrs={'rows':3}))


class ReceiptCancelForm(forms.Form):
    reason = forms.CharField(label='입고 취소 사유',max_length=1000,widget=forms.Textarea(attrs={'rows':3}))
    confirmed = forms.BooleanField(label='원거래와 전량 취소 수량을 확인했습니다.')


class BulkReceiptForm(forms.Form):
    request_id = forms.UUIDField(widget=forms.HiddenInput, initial=uuid.uuid4)
    pasted_rows = forms.CharField(label='A~E열 붙여넣기', required=False, max_length=500000, widget=forms.Textarea(attrs={'rows':12}), help_text='품목코드·품목명·사양·수량·Location 순서입니다.')
    file = forms.FileField(label='또는 입고 Excel 파일 (.xlsx / .xlsm)', required=False)
    def clean(self):
        data = super().clean()
        from .bulk_receipts import parse_receipts
        try: data['rows'] = parse_receipts(pasted=data.get('pasted_rows',''), upload=data.get('file'))
        except forms.ValidationError as error: self.add_error(None,error)
        return data


class SubstituteStockChoice(forms.ModelChoiceField):
    def label_from_instance(self, stock):
        return f'{stock.item.code} | {stock.item.name} | {stock.location} | 가용 {stock.available}'


class SubstitutionForm(forms.Form):
    request_id=forms.UUIDField(widget=forms.HiddenInput,initial=uuid.uuid4)
    requested=forms.ChoiceField(label='고객 요청 코드')
    stock=SubstituteStockChoice(label='빌릴 제품 / 위치',queryset=Stock.objects.none())
    quantity=forms.IntegerField(label='빌릴 수량',min_value=1,max_value=2147483647)
    reason=forms.CharField(label='대체 사유',max_length=1000,widget=forms.Textarea(attrs={'rows':3}))
    confirmed=forms.BooleanField(label='요청 코드와 빌릴 코드·위치·수량을 확인했습니다.')
    def __init__(self,*args,order,**kwargs):
        super().__init__(*args,**kwargs)
        from .substitutions import demand_balance
        from django.db.models import F
        rows=[r for r in demand_balance(order) if r['shortage']]
        self.fields['requested'].choices=[(r['code'],f"{r['code']} · 미배정 {r['shortage']}") for r in rows]
        self.fields['stock'].queryset=Stock.objects.select_related('item').filter(warehouse='Y09',quantity__gt=F('reserved')).order_by('item__code','location','pk')


class SubstitutionDispatchForm(forms.Form):
    shipped_at=forms.DateTimeField(label='실제 대체 출고 시각 (한국 시간)',widget=forms.DateTimeInput(attrs={'type':'datetime-local'}))
    product_confirmed=forms.BooleanField(label='동일 제품이거나 필요한 수정을 완료했고, 실제 대체품이 출고됐습니다.')


class ReceiptSettlementChoice(forms.ModelChoiceField):
    def label_from_instance(self, receipt):
        stock=receipt.arrival_stock or receipt.stock
        return f'{receipt.created_at:%Y-%m-%d} | {stock.item.code} | {stock.location} | 미사용 {receipt.quantity-receipt.settlement_used} / 현재 가용 {stock.available} | {str(receipt.pk)[:8]}'


class SubstitutionSettlementForm(forms.Form):
    request_id=forms.UUIDField(widget=forms.HiddenInput,initial=uuid.uuid4)
    receipt=ReceiptSettlementChoice(label='상환에 사용할 요청 코드 입고 건',queryset=Receipt.objects.none())
    quantity=forms.IntegerField(label='상환 수량',min_value=1,max_value=2147483647)
    note=forms.CharField(label='상환 근거 / 제품 수정 내용',max_length=1000,widget=forms.Textarea(attrs={'rows':3}))
    product_confirmed=forms.BooleanField(label='입고품이 빌린 제품과 동일하거나 필요한 수정을 완료했으며, 입고 위치에서 보충 재고로 전환합니다.')
    def __init__(self,*args,record,**kwargs):
        super().__init__(*args,**kwargs)
        from .models import Receipt
        from django.db.models import Q, F, Sum
        self.fields['receipt'].queryset=Receipt.objects.select_related('stock__item','arrival_stock__item').filter(cancelled_at__isnull=True,stock__item__code=record.requested_code).filter(
            Q(arrival_stock__isnull=True,stock__status='received',created_at__gte=record.allocation.shipped_at)|Q(arrival_stock__isnull=False,transferred_at__gte=record.allocation.shipped_at)).annotate(settlement_used=Sum('substitution_settlements__quantity',default=0)).filter(settlement_used__lt=F('quantity')).filter(Q(arrival_stock__isnull=True,stock__quantity__gt=F('stock__reserved'))|Q(arrival_stock__quantity__gt=F('arrival_stock__reserved'))).order_by('-created_at','pk')


class SubstitutionErpForm(forms.Form):
    decision=forms.ChoiceField(label='회사 ERP 정산 확인',choices=[('confirm','회사 ERP 코드·수량 정리 확인'),('undo','확인 취소')])
    note=forms.CharField(label='확인 / 확인 취소 근거',max_length=1000,widget=forms.Textarea(attrs={'rows':3}))
    confirmed=forms.BooleanField(label='회사 ERP 자료를 확인했습니다. 이 작업은 재고 수량을 변경하지 않습니다.')


class SubstitutionCancelForm(forms.Form):
    note=forms.CharField(label='대체 예약 취소 사유',max_length=1000,widget=forms.Textarea(attrs={'rows':2}))
    confirmed=forms.BooleanField(label='아직 실물 대체 출고 전이며 예약만 취소합니다.')
