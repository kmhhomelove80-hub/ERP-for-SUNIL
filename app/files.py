"""수식이나 매크로를 실행하지 않는 파일 입출력."""
import csv
import io
import zipfile
from pathlib import PurePosixPath
from openpyxl import load_workbook

MAX_EXPANDED = 100 * 1024 * 1024

def checked_zip(data):
    z = zipfile.ZipFile(io.BytesIO(data))
    if sum(i.file_size for i in z.infolist()) > MAX_EXPANDED:
        raise ValueError('압축 해제 크기가 너무 큽니다. 100MB 이하 파일을 사용하세요.')
    return z

def read_book(data, name):
    if name.lower().endswith('.zip'):
        with checked_zip(data) as z:
            entries = [x for x in z.namelist() if not x.startswith('__MACOSX/') and PurePosixPath(x).suffix.lower() in ('.xlsb', '.xlsx', '.xlsm')]
            if len(entries) != 1:
                raise ValueError('ZIP에는 엑셀 파일을 하나만 넣어 주세요.')
            name = entries[0]
            data = z.read(name)
    if name.lower().endswith('.xlsb'):
        checked_zip(data).close()
        from pyxlsb import open_workbook
        import tempfile
        with tempfile.NamedTemporaryFile(suffix='.xlsb') as f:
            f.write(data); f.flush()
            with open_workbook(f.name) as w:
                result = {}
                for sheet in w.sheets:
                    with w.get_sheet(sheet) as s:
                        rows = []
                        for row in s.rows():
                            if not row: continue
                            while len(rows) <= row[0].r: rows.append([])
                            values = [None] * (max(c.c for c in row) + 1)
                            for c in row: values[c.c] = c.v
                            rows[row[0].r] = values
                        result[sheet] = rows
                return result
    if not name.lower().endswith(('.xlsx', '.xlsm')):
        raise ValueError('기준 파일은 XLSB, XLSX, XLSM 또는 해당 파일을 담은 ZIP을 지원합니다.')
    checked_zip(data).close()
    w = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    try:
        return {s.title: [list(r) for r in s.iter_rows(values_only=True)] for s in w}
    finally:
        w.close()

def read_erp(data, name):
    if name.lower().endswith(('.xlsx', '.xlsm', '.xlsb', '.zip')):
        book = read_book(data, name)
        return next(iter(book.values()))
    # ERP에서 내려받은 .xls는 실제로 CP949 탭 구분 텍스트입니다.
    if data.startswith(bytes.fromhex('d0cf11e0')):
        raise ValueError('구형 바이너리 XLS는 XLSX로 저장한 뒤 업로드하세요.')
    for encoding in ('utf-8-sig', 'cp949'):
        try:
            text = data.decode(encoding)
            return list(csv.reader(io.StringIO(text), delimiter='\t'))
        except UnicodeDecodeError:
            pass
    raise ValueError('ERP 파일 인코딩을 읽을 수 없습니다.')
