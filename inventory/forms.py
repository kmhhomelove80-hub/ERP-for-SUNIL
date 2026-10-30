import uuid
from django import forms
from .models import Item, Stock

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
    pasted_rows = forms.CharField(label='ERP 코드 · 수량 붙여넣기', max_length=100000, widget=forms.Textarea(attrs={'rows':12,'placeholder':'ERP001\t10\nERP002\t5'}),help_text='엑셀의 두 열을 복사하세요. 헤더 없이 최대 500행, 중복 품번은 합산합니다.')
    def clean_pasted_rows(self):
        from .picking import parse_paste
        return parse_paste(self.cleaned_data['pasted_rows'])

class PickingConfirmForm(forms.Form):
    preview_token = forms.CharField(widget=forms.HiddenInput,max_length=500000)
    allow_shortage = forms.BooleanField(label='부족 수량을 확인했으며, 확보 가능한 수량만 예약합니다.', required=False)

class AllocationActionForm(forms.Form):
    from .models import Allocation
    allocations = forms.ModelMultipleChoiceField(label='대상 행',queryset=Allocation.objects.filter(state='reserved'),widget=forms.CheckboxSelectMultiple)
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
    destination = forms.CharField(label='이동할 위치',max_length=150)
    quantity = forms.IntegerField(label='요청 수량',min_value=1,max_value=2147483647)
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
    action = forms.ChoiceField(label='작업 종류',required=False,choices=[('','전체'),('initial_import','초기 이관'),('receipt','입고'),('receipt_cancel','입고 취소'),('arrival','도착 확인'),('reserve','예약'),('cancel','예약 취소'),('release','출고 확정'),('picking_confirm','피킹 등록'),('movement_requested','이동 요청'),('movement','이동 처리'),('erp_snapshot','ERP 대조 등록'),('reconciliation_review','차이 분석 기록'),('legacy_archive','과거 이력 보관 이관')])
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
    layout = forms.ChoiceField(label='파일 형식',choices=[('standard','표준 ERP재고 양식'),('legacy','기존 SUNIL 파일의 재고현황 시트')])
    file = forms.FileField(label='ERP 재고 파일 (.xlsx / .xlsm)')
    confirmed_mapping = forms.BooleanField(label='기존 형식의 I:R은 Y02, W:AF는 Y09임을 확인했습니다.',required=False)
    def clean(self):
        data=super().clean()
        if data.get('layout')=='legacy' and not data.get('confirmed_mapping'):
            self.add_error('confirmed_mapping','기존 형식의 창고 배치를 확인하세요.')
        if data.get('file') and data['file'].size>10*1024*1024:
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
