"""ERP reference snapshots never overwrite warehouse inventory."""
import hashlib
import io
import math
from collections import defaultdict
from zipfile import ZipFile, BadZipFile
from django.core.exceptions import ValidationError
from django.db.models import Sum
from django.utils import timezone
from openpyxl import load_workbook, Workbook
from .locking import warehouse_transaction
from .models import ErpSnapshot, ReconciliationRow, Stock
from .services import audit

HEADERS = ['창고','ERP 코드','현재고','출하대기','모델명']

def validate_rows(rows):
    seen=set()
    if not rows or len(rows)>10000: raise ValidationError('ERP 자료는 1~10,000행이어야 합니다.')
    for row in rows:
        key=(row['warehouse'],row['code'])
        if row['warehouse'] not in ['Y09','Y02'] or not row['code'] or len(row['code'])>100 or row['code'].startswith(('=','#')) or len(row['name'])>250:
            raise ValidationError('창고(Y09/Y02), ERP 코드 또는 모델명을 확인하세요.')
        if key in seen: raise ValidationError(f'{row["warehouse"]} {row["code"]}: 중복 행입니다. 원본에서 확인하세요.')
        seen.add(key)
        for field in ['quantity','shipping_pending']:
            value=row[field]
            if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or int(value)!=value or not -2147483648<=value<=2147483647:
                raise ValidationError(f'{row["code"]}: 현재고/출하대기는 정수 범위 내 원본 값이어야 합니다.')
            row[field]=int(value)
    return rows

def inspect_erp_file(upload, layout):
    content=upload.read(10*1024*1024+1)
    if len(content)>10*1024*1024: raise ValidationError('파일은 10MB 이하여야 합니다.')
    try:
        with ZipFile(io.BytesIO(content)) as zipped:
            if sum(i.file_size for i in zipped.infolist())>100*1024*1024:
                raise ValidationError('압축 해제 크기가 너무 큽니다.')
        workbook=load_workbook(io.BytesIO(content),read_only=True,data_only=False,keep_links=False)
    except (BadZipFile,KeyError,OSError,ValueError) as error:
        raise ValidationError('유효한 xlsx/xlsm 파일이 아닙니다.') from error
    rows=[]
    try:
        if layout=='standard':
            if 'ERP재고' not in workbook.sheetnames: raise ValidationError('ERP재고 시트가 필요합니다. 제공 양식을 사용하세요.')
            sheet=workbook['ERP재고']
            if sheet.max_row>10001 or sheet.max_column>5: raise ValidationError('양식의 5개 열, 최대 10,000행을 사용하세요.')
            if [sheet.cell(1,c).value for c in range(1,6)]!=HEADERS: raise ValidationError('헤더가 양식과 다릅니다.')
            for warehouse,code,quantity,pending,name in sheet.iter_rows(min_row=2,max_col=5,values_only=True):
                if all(v is None for v in [warehouse,code,quantity,pending,name]): continue
                rows.append(dict(warehouse=str(warehouse or '').strip(),code=str(code or '').strip(),quantity=quantity,shipping_pending=pending,name=str(name or '').strip()))
        elif layout=='legacy':
            if '재고현황' not in workbook.sheetnames: raise ValidationError('재고현황 시트가 없습니다.')
            sheet=workbook['재고현황']
            if sheet.max_row>10001: raise ValidationError('ERP 원본이 10,000행을 초과합니다.')
            for code_col,qty_col,pending_col,warehouse in [(9,16,18,'Y02'),(23,30,32,'Y09')]:
                if sheet.cell(1,code_col).value!='품목코드' or sheet.cell(1,qty_col).value!='현재고' or sheet.cell(1,pending_col).value!='출하대기':
                    raise ValidationError('재고현황의 열 구조가 예상과 다릅니다.')
                for values in sheet.iter_rows(min_row=2,min_col=code_col,max_col=pending_col,values_only=True):
                    code=values[0]
                    if code is None: continue
                    rows.append(dict(warehouse=warehouse,code=str(code).strip(),name=str(values[1] or '').strip(),quantity=values[qty_col-code_col],shipping_pending=values[pending_col-code_col]))
        else: raise ValidationError('자료 형식을 확인하세요.')
        validate_rows(rows)
        return dict(digest=hashlib.sha256(content).hexdigest(),source_name=str(upload.name).replace('\\','/').split('/')[-1][:255],rows=rows)
    finally: workbook.close()

def template_bytes():
    book=Workbook();sheet=book.active;sheet.title='ERP재고';sheet.append(HEADERS)
    sheet.column_dimensions['A'].width=12;sheet.column_dimensions['B'].width=28
    sheet.column_dimensions['C'].width=14;sheet.column_dimensions['D'].width=14;sheet.column_dimensions['E'].width=35
    stream=io.BytesIO();book.save(stream);return stream.getvalue()

@warehouse_transaction
def create_snapshot(*, request_id, label, erp_as_of, plan, actor):
    rows=validate_rows(plan['rows'])
    if timezone.is_naive(erp_as_of) or erp_as_of>timezone.now(): raise ValidationError('ERP 기준 시각을 확인하세요.')
    previous=ErpSnapshot.objects.filter(pk=request_id).first()
    if previous:
        if previous.actor_id!=actor.pk or previous.digest!=plan['digest'] or previous.label!=label or previous.erp_as_of!=erp_as_of:
            raise ValidationError('이미 사용한 요청번호입니다.')
        return previous
    snapshot=ErpSnapshot.objects.create(id=request_id,label=label,erp_as_of=erp_as_of,actor=actor,digest=plan['digest'],source_name=plan['source_name'])
    stock_totals=defaultdict(lambda:dict(received=0,expected=0,reserved=0,name=''))
    for s in Stock.objects.select_related('item').filter(warehouse='Y09'):
        d=stock_totals[s.item.code];d[s.status]+=s.quantity;d['reserved']+=s.reserved;d['name']=s.item.name
    included=set()
    objects=[]
    for row in rows:
        d=stock_totals.get(row['code'],{}) if row['warehouse']=='Y09' else {}
        objects.append(ReconciliationRow(snapshot=snapshot,warehouse=row['warehouse'],code=row['code'],name=row['name'],erp_quantity=row['quantity'],shipping_pending=row['shipping_pending'],received=d.get('received',0),expected=d.get('expected',0),reserved=d.get('reserved',0)))
        if row['warehouse']=='Y09': included.add(row['code'])
    # Only compare Y09 when this batch actually contains Y09. Y02 is reference.
    if any(r['warehouse']=='Y09' for r in rows):
        for code,d in stock_totals.items():
            if code not in included and (d['received'] or d['expected'] or d['reserved']):
                objects.append(ReconciliationRow(snapshot=snapshot,warehouse='Y09',code=code,name=d['name'],**{k:d[k] for k in ['received','expected','reserved']}))
    ReconciliationRow.objects.bulk_create(objects)
    audit(actor,'erp_snapshot',snapshot.pk,label=label,rows=len(objects),quantity=sum(r['quantity'] for r in rows))
    return snapshot

def latest_reference(warehouse,codes):
    snapshot=ErpSnapshot.objects.filter(rows__warehouse=warehouse).order_by('-created_at','-pk').distinct().first()
    if not snapshot: return {},None
    rows=snapshot.rows.filter(warehouse=warehouse,code__in=codes).values('code','erp_quantity','shipping_pending')
    return {r['code']:r for r in rows if r['erp_quantity'] is not None},snapshot

@warehouse_transaction
def update_review(*, row_id, state, reason, action_note, actor):
    row=ReconciliationRow.objects.select_for_update().get(pk=row_id)
    if not row.needs_review: raise ValidationError('차이 또는 ERP 누락 항목만 사유를 기록할 수 있습니다.')
    if state not in ['open','explained','resolved'] or (state!='open' and not reason.strip()) or (state=='resolved' and not action_note.strip()):
        raise ValidationError('원인과 조치 내용을 확인하세요.')
    previous=dict(state=row.review_state,reason=row.reason,action_note=row.action_note)
    row.review_state=state;row.reason=reason.strip();row.action_note=action_note.strip();row.updated_by=actor;row.updated_at=timezone.now()
    row.save(update_fields=['review_state','reason','action_note','updated_by','updated_at'])
    audit(actor,'reconciliation_review',row.pk,code=row.code,warehouse=row.warehouse,state=state,reason=row.reason,action_note=row.action_note,before=previous)
    return row


def inspect_erp_exports(files):
    """User's ERP exports are CP949 tab-separated text named .xls."""
    from .tabular import read_table, cell, integer
    rows, digests, names = [], [], []
    for warehouse, upload in files.items():
        if upload is None: continue
        raw, content = read_table(upload)
        headers = [cell(v) for v in raw[0]] if raw else []
        required = ['품목코드','품목명','현재고','출하대기']
        if any(headers.count(h) != 1 for h in required):
            raise ValidationError(f'{warehouse}: 품목코드·품목명·현재고·출하대기 헤더가 필요합니다.')
        indexes = {h:headers.index(h) for h in required}
        for number, row in enumerate(raw[1:], 2):
            if not any(cell(v) for v in row): continue
            if len(row) <= max(indexes.values()):
                raise ValidationError(f'{warehouse} {number}행: 열이 누락됐습니다.')
            code = cell(row[indexes['품목코드']])
            if not code and cell(row[0]) == '합계': continue
            if not code: raise ValidationError(f'{warehouse} {number}행: 품목코드가 없습니다.')
            rows.append(dict(warehouse=warehouse, code=code, name=cell(row[indexes['품목명']]),
                             quantity=integer(row[indexes['현재고']]), shipping_pending=integer(row[indexes['출하대기']])))
        digests.append(warehouse+':'+hashlib.sha256(content).hexdigest())
        names.append(warehouse+':'+str(upload.name).replace('\\','/').split('/')[-1])
    validate_rows(rows)
    return dict(rows=rows, digest=hashlib.sha256('|'.join(digests).encode()).hexdigest(), source_name=' / '.join(names)[:255])
