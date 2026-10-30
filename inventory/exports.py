"""Read-only Excel outputs with explicit text cells (no formula execution)."""
import io
from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from django.core.exceptions import ValidationError

MAX_EXPORT_ROWS=50000

def workbook_bytes(title,headers,rows):
    book=Workbook(write_only=True);sheet=book.create_sheet(title)
    sheet.freeze_panes='A2'
    def append(values):
        result=[]
        for value in values:
            cell=WriteOnlyCell(sheet,value=value)
            if isinstance(value,str):cell.data_type='s'
            result.append(cell)
        sheet.append(result)
    append(headers)
    count=0
    try:
        for count,row in enumerate(rows,1):
            if count>MAX_EXPORT_ROWS:raise ValidationError('내보내기는 50,000행 이내로 검색 범위를 좁혀주세요.')
            append(row)
        from openpyxl.utils import get_column_letter
        sheet.auto_filter.ref=f'A1:{get_column_letter(len(headers))}{count+1}'
        stream=io.BytesIO();book.save(stream);return stream.getvalue()
    finally:
        book.close()
