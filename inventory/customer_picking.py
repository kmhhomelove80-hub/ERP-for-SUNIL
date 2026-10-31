"""Customer rows are retained; print consumes saved allocations, never reserves twice."""
from collections import OrderedDict
from itertools import groupby
from django.core.exceptions import ValidationError
from .tabular import read_table, cell, integer

HEADERS = ['POR번호','호선','블록','품명','수량','ERP CODE']


def parse_customer(*, pasted='', upload=None):
    if bool(pasted.strip()) == bool(upload):
        raise ValidationError('고객 자료 붙여넣기 또는 파일 중 하나를 선택하세요.')
    raw = read_table(upload)[0] if upload else [r.split('\t') for r in pasted.splitlines() if r.strip()]
    offset, start = 0, 0
    for index, row in enumerate(raw[:3]):
        values = [cell(v) for v in row]
        if 'POR번호' in values:
            offset, start = values.index('POR번호'), index+1
            if values[offset:offset+6] != HEADERS:
                raise ValidationError('고객 자료 헤더는 POR번호·호선·블록·품명·수량·ERP CODE 순서입니다.')
            break
    rows = []
    for index, raw_row in enumerate(raw[start:], start+1):
        values = [cell(v) for v in raw_row[offset:offset+6]]
        if not any(values): continue
        if len(values)!=6:
            raise ValidationError(f'{index}행: 고객 자료 6열을 확인하세요.')
        por, hull, block, name, qty, code = values
        if not code or len(code)>100 or code.startswith(('=', '#')) or any(len(v)>2000 for v in [por,hull,block,name]):
            raise ValidationError(f'{index}행: ERP CODE와 고객 정보를 확인하세요.')
        rows.append(dict(por=por, hull=hull, block=block, name=name, quantity=integer(qty,positive=True), code=code))
    if not 1<=len(rows)<=500: raise ValidationError('고객 자료는 1~500행입니다.')
    return rows


def customer_print_groups(order):
    customer = order.payload.get('customer_rows', [])
    pools = {}
    for allocation in order.allocations.select_related('stock__item','substitution').exclude(state='cancelled').order_by('stock_id','pk'):
        detail = allocation.display_details
        from .substitutions import requested_code
        actual=detail['code'] if hasattr(allocation,'substitution') else ''
        pools.setdefault(requested_code(allocation), []).append([detail['location'], allocation.quantity, detail['status'],actual])
    groups=[]
    # Match the original N/O merged blocks. Non-adjacent repeated codes stay in
    # their original order and consume the remaining saved allocation pool.
    for code, source in groupby(customer, key=lambda r:r['code']):
        rows=list(source);total=sum(r['quantity'] for r in rows);remaining=total
        locations=OrderedDict();expected=False
        for entry in pools.get(code,[]):
            take=min(entry[1],remaining)
            if take:
                key=(entry[0],entry[3])
                locations[key]=locations.get(key,0)+take
                expected=expected or entry[2]=='expected'
                entry[1]-=take;remaining-=take
            if not remaining:break
        groups.append(dict(rows=rows,total=total,locations=[dict(location=k[0],quantity=v,**({'actual_code':k[1]} if k[1] else {})) for k,v in locations.items()],shortage=remaining,expected=expected))
    return groups
