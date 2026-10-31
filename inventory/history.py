def event_summary(event):
    d = event.detail
    if event.action.startswith('substitution_'):
        return f"{d.get('requested_code','')} ← {d.get('actual_code','')}" + (f" · {d['quantity']}개" if 'quantity' in d else '') + f" · {d.get('note',d.get('reason',''))}"
    if event.action in ['movement','movement_requested']:
        source = d.get('source_location',d.get('location',''))
        destination = d.get('destination','')
        state = {'completed':'완료','pending':'대기','rejected':'거부'}.get(d.get('state'),'요청 등록')
        return f"{source} → {destination} · {state}" + (f" · {d['reason']}" if d.get('reason') else '')
    if event.action == 'initial_import':
        return f"초기 재고 {d.get('stock_rows',0)}행 / 예약 {d.get('allocation_rows',0)}행"
    if event.action=='erp_snapshot': return f"{d.get('label','')} · {d.get('rows',0)}행 대조"
    if event.action=='reconciliation_review': return f"{d.get('warehouse','')} · {d.get('reason','')} · {d.get('action_note','')}"
    if event.action=='receipt_cancel': return f"{d.get('location','')} · {d.get('reason','')}"
    if event.action == 'receipt_batch': return f"일괄 입고 {d.get('count',0)}행 · 총 {d.get('quantity',0)}개"
    if event.action == 'receipt':
        return f"{d.get('location','')} · " + ('입고 예정 등록' if d.get('status')=='expected' else '입고 등록')
    if event.action == 'arrival':
        return f"{d.get('source_location','')} → {d.get('destination','')} · 총량 유지, 예약 이전"
    if event.action == 'picking_confirm':
        return f"{d.get('project','')} · 부족 품목 {len(d.get('shortages',[]))}종"
    return str(d.get('project',''))
