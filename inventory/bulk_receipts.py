import uuid
from django.core.exceptions import ValidationError
from .tabular import read_table, cell, integer
from .locking import warehouse_transaction
from .models import Item, AuditEvent, Stock
from .services import receive_stock, audit


def parse_receipts(*, pasted='', upload=None):
    if bool(pasted.strip()) == bool(upload):
        raise ValidationError('A~E열 붙여넣기 또는 파일 업로드 중 하나를 선택하세요.')
    raw = read_table(upload)[0] if upload else [r.split('\t') for r in pasted.splitlines() if r.strip()]
    rows = []
    for index, source in enumerate(raw, 1):
        values = [cell(v) for v in source]
        if not any(values): continue
        if (not any(values[1:]) and '입고' in values[0] and 'LIST' in values[0].upper()) or (values[0] in ['품목코드', 'ERP 코드', 'ITEM No.']):
            if not rows and index <= 3: continue
        if len(values) < 5 or any(values[5:]):
            raise ValidationError(f'{index}행: A~E의 5열만 입력하세요(코드·모델·사양·수량·Location).')
        code, name, specification, qty, location = values[:5]
        if not code or len(code)>100 or code.startswith(('=', '#')) or len(name)>250 or len(specification)>5000 or not location or len(location)>150:
            raise ValidationError(f'{index}행: 코드·모델·사양·Location을 확인하세요.')
        rows.append(dict(code=code, name=name, specification=specification, quantity=integer(qty, positive=True), location=location))
    if not 1 <= len(rows) <= 1000:
        raise ValidationError('1~1,000행을 등록할 수 있습니다.')
    return rows


@warehouse_transaction
def receive_batch(*, request_id, rows, actor):
    # Normalize again, including signed preview data. Keep repeated code/location
    # rows as individual receipts so each original line remains traceable.
    normalized = parse_receipts(pasted='\n'.join('\t'.join(str(r[k]) for k in ['code','name','specification','quantity','location']) for r in rows))
    prior = AuditEvent.objects.filter(action='receipt_batch', reference=str(request_id)).first()
    if prior:
        if prior.actor_id != actor.pk or prior.detail.get('rows') != normalized:
            raise ValidationError('이미 사용한 요청번호입니다.')
        return
    for i, row in enumerate(normalized):
        item, _ = Item.objects.get_or_create(code=row['code'], defaults={'name':row['name'], 'specification':row['specification']})
        receive_stock(item_id=item.pk, location=row['location'], quantity=row['quantity'], status=Stock.Status.RECEIVED,
                      expected_date=None, sender='', request_id=uuid.uuid5(request_id, str(i)), actor=actor)
    audit(actor, 'receipt_batch', request_id, rows=normalized, count=len(normalized), quantity=sum(r['quantity'] for r in normalized))
