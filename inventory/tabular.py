"""Bounded data-only Excel/ERP text readers; never evaluate formulas/macros."""
import csv
import io
from zipfile import ZipFile, BadZipFile
from decimal import Decimal, InvalidOperation
from django.core.exceptions import ValidationError
from openpyxl import load_workbook

MAX_ROWS = 10000


def read_table(upload):
    data = upload.read(10 * 1024 * 1024 + 1)
    if len(data) > 10 * 1024 * 1024:
        raise ValidationError('10MB 이하 파일을 사용하세요.')
    if data.startswith(b'PK'):
        try:
            with ZipFile(io.BytesIO(data)) as archive:
                if sum(f.file_size for f in archive.infolist()) > 100 * 1024 * 1024:
                    raise ValidationError('압축 해제 크기가 너무 큽니다.')
            book = load_workbook(io.BytesIO(data), read_only=True, data_only=False, keep_links=False)
            try:
                sheet = book.active
                if sheet.max_row > MAX_ROWS + 5 or sheet.max_column > 100:
                    raise ValidationError('최대 10,000행/100열 자료를 사용하세요.')
                rows = list(sheet.iter_rows(values_only=True))
            finally:
                book.close()
        except (BadZipFile, KeyError, ValueError, OSError) as error:
            raise ValidationError('Excel 파일을 읽을 수 없습니다.') from error
    else:
        if data.startswith(b'\xd0\xcf\x11\xe0'):
            raise ValidationError('이진 XLS는 XLSX로 저장하세요. ERP의 탭 구분 XLS 추출 파일은 그대로 지원합니다.')
        text = None
        for encoding in ['utf-8-sig', 'cp949']:
            try:
                text = data.decode(encoding); break
            except UnicodeDecodeError:
                pass
        if text is None or '\x00' in text or '\t' not in text:
            raise ValidationError('XLSX 또는 탭 구분 ERP 추출 파일을 사용하세요.')
        try:
            rows = list(csv.reader(io.StringIO(text), delimiter='\t'))
        except csv.Error as error:
            raise ValidationError('탭 구분 자료를 읽을 수 없습니다.') from error
    if len(rows) > MAX_ROWS + 5 or any(len(row) > 100 for row in rows):
        raise ValidationError('최대 10,000행/100열 자료를 사용하세요.')
    return rows, data


def cell(value):
    return '' if value is None else str(value).strip()


def integer(value, *, positive=False):
    text = cell(value).replace(',', '')
    try:
        number = Decimal(text)
        if not number.is_finite() or number != number.to_integral_value():
            raise InvalidOperation
        result = int(number)
    except (InvalidOperation, ValueError, OverflowError):
        raise ValidationError(f'정수 수량을 확인하세요: {cell(value)[:40]}')
    if not (-2147483648 <= result <= 2147483647) or (positive and result <= 0):
        raise ValidationError('수량이 허용 범위를 벗어났습니다.')
    return result
