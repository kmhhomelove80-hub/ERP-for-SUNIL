"""Read legacy workbook without evaluating formulas or executing VBA."""
import hashlib
from collections import Counter
from pathlib import Path
from django.core.exceptions import ValidationError
from .locking import warehouse_transaction
from openpyxl import load_workbook
from .models import Item, Stock, Allocation, ImportBatch
from .services import audit
from .transaction_details import stock_details

MAX_ROWS = 50000

def literal(value, label, limit):
    value = '' if value is None else str(value).strip()
    if not value or value.startswith(('=', '#')) or len(value) > limit:
        raise ValidationError(f'{label}: 빈 값, 수식, 오류 또는 길이 초과 값입니다.')
    return value

def quantity(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0 or int(value) != value or value > 2147483647:
        raise ValidationError(f'{label}: 0 이상의 정수 수량이 필요합니다.')
    return int(value)

def inspect_workbook(path, expected_locations=()):
    path = Path(path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    workbook = load_workbook(path, read_only=True, data_only=False, keep_links=False)
    stocks, allocations, warnings = [], [], []
    try:
        if not {'재고_rawdata', 'Picking List'} <= set(workbook.sheetnames):
            raise ValidationError('재고_rawdata와 Picking List 시트가 필요합니다.')
        raw, pick = workbook['재고_rawdata'], workbook['Picking List']
        if max(raw.max_row, pick.max_row) > MAX_ROWS:
            raise ValidationError('이관 검증 범위(50,000행)를 초과했습니다.')
        if raw['B7'].value != 'ITEM No.' or pick['BA7'].value != 'ITEM No.':
            raise ValidationError('원본 열 구조가 예상 형식과 다릅니다.')
        expected_locations = set(expected_locations)
        seen, metadata, reserved = set(), {}, Counter()
        for row_number, row in enumerate(raw.iter_rows(min_row=8, max_col=6, values_only=True), 8):
            _, code, name, spec, qty, location = row
            if code is None and all(v is None for v in row[1:]): continue
            code = literal(code, f'재고 {row_number} 품번', 100)
            location = literal(location, f'재고 {row_number} 위치', 150)
            key = (code, location)
            if key in seen: raise ValidationError(f'재고 {row_number}: 품번+위치가 중복됩니다.')
            seen.add(key)
            qty = quantity(qty, f'재고 {row_number} 수량')
            name, spec = str(name or '').strip(), str(spec or '').strip()
            if len(name) > 250: raise ValidationError(f'재고 {row_number}: 모델명이 너무 깁니다.')
            if code in metadata and metadata[code] != (name, spec):
                warnings.append(f'재고 {row_number}: 같은 품번의 설명 차이. 품목 기준 설명은 첫 행을 쓰고 행별 원본 설명은 별도 보존합니다.')
            metadata.setdefault(code, (name, spec))
            if '입고' in location and location not in expected_locations:
                raise ValidationError(f'재고 {row_number}: 예정 여부를 --expected-location {location!r} 옵션으로 명시하세요.')
            stocks.append(dict(code=code, name=name, specification=spec, quantity=qty, location=location, status='expected' if location in expected_locations else 'received'))
            # Ignore stale or broken calculated cells; allocations are recomputed.
        unknown = expected_locations - {s['location'] for s in stocks}
        if unknown: raise ValidationError('예정 위치 설정 중 원본에 없는 위치가 있습니다.')
        for n, row in enumerate(pick.iter_rows(min_row=8, min_col=53, max_col=59, values_only=True), 8):
            code, _, _, qty, location, seq, project = row
            if all(v is None for v in row): continue
            code = literal(code, f'예약 {n} 품번', 100)
            location = literal(location, f'예약 {n} 위치', 150)
            qty = quantity(qty, f'예약 {n} 수량')
            if qty == 0: raise ValidationError(f'예약 {n}: 수량이 0입니다.')
            project = literal(project, f'예약 {n} 프로젝트', 250)
            seq = literal(seq, f'예약 {n} SEQ', 40)
            if (code, location) not in seen: raise ValidationError(f'예약 {n}: 해당 품목/위치 재고가 없습니다.')
            reserved[code, location] += qty
            allocations.append(dict(code=code, location=location, quantity=qty, project=project, legacy_seq=seq))
        for stock in stocks:
            stock['reserved'] = reserved[stock['code'], stock['location']]
            if stock['reserved'] > stock['quantity']:
                raise ValidationError('예약량이 현재고보다 큰 항목이 있습니다.')
        summary = dict(stock_rows=len(stocks), item_count=len(metadata), quantity=sum(s['quantity'] for s in stocks), allocation_rows=len(allocations), reserved=sum(a['quantity'] for a in allocations), expected_quantity=sum(s['quantity'] for s in stocks if s['status']=='expected'))
        if not stocks: raise ValidationError('이관할 재고가 없습니다.')
        return dict(digest=digest, source_name=path.name, stocks=stocks, allocations=allocations, summary=summary, warnings=warnings)
    finally:
        workbook.close()

@warehouse_transaction
def apply_import(plan, actor):
    if ImportBatch.objects.filter(digest=plan['digest']).exists():
        raise ValidationError('이미 반영한 파일입니다.')
    if Stock.objects.exists() or Allocation.objects.exists():
        raise ValidationError('초기 이관은 재고와 예약이 없는 DB에서만 가능합니다.')
    index, imported_items = {}, {}
    for row in plan['stocks']:
        item = imported_items.get(row['code'])
        if item is None:
            item, created = Item.objects.get_or_create(code=row['code'], defaults={'name':row['name'], 'specification':row['specification']})
            if not created and (item.name, item.specification) != (row['name'], row['specification']):
                raise ValidationError('기존 품목 정보와 이관 자료가 다릅니다.')
            imported_items[row['code']] = item
        index[row['code'], row['location']] = Stock.objects.create(item=item, warehouse='Y09', location=row['location'], status=row['status'], quantity=row['quantity'], reserved=row['reserved'],legacy_name=row['name'],legacy_specification=row['specification'])
    for row in plan['allocations']:
        stock=index[row['code'],row['location']]
        Allocation.objects.create(stock=stock, project=row['project'], quantity=row['quantity'], legacy_seq=row['legacy_seq'],assigned_details=stock_details(stock))
    batch = ImportBatch.objects.create(digest=plan['digest'],source_name=plan['source_name'],actor=actor,summary=plan['summary'])
    audit(actor,'initial_import',batch.pk,**plan['summary'])
    return batch
