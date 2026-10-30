"""Preserve old history verbatim without replaying stock-changing operations."""
import hashlib
import io
from collections import Counter
from datetime import datetime,date
from zipfile import ZipFile,BadZipFile
from openpyxl import load_workbook
from django.core.exceptions import ValidationError
from .locking import warehouse_transaction
from .models import LegacyArchiveBatch,LegacyArchiveRecord
from .services import audit

def inspect_archive(path):
    from pathlib import Path
    path=Path(path);content=path.read_bytes()
    if len(content)>10*1024*1024:raise ValidationError('원본은 10MB 이하여야 합니다.')
    try:
        with ZipFile(io.BytesIO(content)) as zipped:
            if sum(x.file_size for x in zipped.infolist())>100*1024*1024:raise ValidationError('원본 압축 해제 크기가 너무 큽니다.')
        book=load_workbook(io.BytesIO(content),read_only=True,data_only=False,keep_links=False)
    except (BadZipFile,KeyError,OSError,ValueError) as error:raise ValidationError('유효한 Excel 파일이 아닙니다.') from error
    records=[]
    def normalized(value):
        return value.isoformat() if isinstance(value,(datetime,date)) else value
    def add(kind,row,code,description,keys,values):
        records.append(dict(kind=kind,source_row=row,code=str(code or '').strip()[:100],description=str(description or ''),payload={k:normalized(v) for k,v in zip(keys,values)}))
    try:
        for sheet in book:
            if sheet.title in ['BACKUP','작업로그','재고이동'] and sheet.max_row>50000:raise ValidationError('이력 시트는 50,000행 이하만 지원합니다.')
        if {'BACKUP','작업로그'}<=set(book.sheetnames):
            backup=book['BACKUP']
            if backup['A1'].value!='ITEM No.' or backup['K1'].value!='ITEM No.':raise ValidationError('BACKUP 열 구성을 확인하세요.')
            for n,values in enumerate(backup.iter_rows(min_row=2,max_col=17,values_only=True),2):
                if values[0] is not None:add('receipt',n,values[0],values[2],['code','model','specification','quantity','location','seq'],values[:6])
                if values[10] is not None:add('picking',n,values[10],values[16],['code','model','specification','quantity','location','seq','project'],values[10:17])
            logs=book['작업로그']
            for n,values in enumerate(logs.iter_rows(min_row=2,max_col=4,values_only=True),2):
                if any(v is not None for v in values):add('log',n,'',values[3],['timestamp','username','action','detail'],values)
        elif '재고이동' in book.sheetnames:
            moves=book['재고이동']
            if [moves.cell(1,c).value for c in range(1,9)]!=['ID','ERP_CODE','현재위치','이동할위치','요청수량','요청자','상태','완료 시각']:
                raise ValidationError('AppSheet 이동 이력 열 구성을 확인하세요.')
            for n,values in enumerate(moves.iter_rows(min_row=2,max_col=8,values_only=True),2):
                if values[1] is not None:add('movement',n,values[1],values[6],['original_id','code','source_location','destination','quantity','requested_by','state','raw_completed_at'],values)
        else:raise ValidationError('기존 ERP 이력 또는 AppSheet 이동 파일이 아닙니다.')
    finally:book.close()
    return dict(digest=hashlib.sha256(content).hexdigest(),source_name=path.name,records=records,counts=dict(Counter(r['kind'] for r in records)))

@warehouse_transaction
def apply_archive(plan,actor):
    existing=LegacyArchiveBatch.objects.filter(digest=plan['digest']).first()
    if existing:return existing
    # These are one-time migration archives, not periodic incremental imports.
    kinds=set(plan['counts'])
    if LegacyArchiveRecord.objects.filter(kind__in=kinds).exists():raise ValidationError('같은 종류의 과거 이력이 이미 있습니다. 증분/중복 이관은 별도 검토해야 합니다.')
    batch=LegacyArchiveBatch.objects.create(digest=plan['digest'],source_name=plan['source_name'],actor=actor,counts=plan['counts'])
    LegacyArchiveRecord.objects.bulk_create([LegacyArchiveRecord(batch=batch,**r) for r in plan['records']],batch_size=500)
    audit(actor,'legacy_archive',batch.pk,counts=plan['counts'])
    return batch
