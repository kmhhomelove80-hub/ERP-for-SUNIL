"""Quantity checks shared by preview and both saved picking print formats."""
from collections import Counter


def picking_totals(requested_rows, allocations):
    normal = substitute = 0
    assigned = Counter()
    for row in allocations:
        assigned[row.get('requested_code', row.get('code'))] += row['quantity']
        if row.get('substitute'): substitute += row['quantity']
        else: normal += row['quantity']
    allocated = normal + substitute
    if requested_rows is None:
        return dict(requested=None, allocated=allocated, normal=normal, substitute=substitute, shortage=None, excess=None, matches=False)
    requested = Counter()
    for row in requested_rows: requested[row['code']] += row['quantity']
    shortage = sum(max(requested[c] - assigned[c], 0) for c in requested)
    excess = sum(max(assigned[c] - requested[c], 0) for c in assigned)
    return dict(requested=sum(requested.values()), allocated=allocated, normal=normal, substitute=substitute,
                shortage=shortage, excess=excess, matches=shortage == 0 and excess == 0)


def order_totals(order, allocations=None):
    from .substitutions import requested_code
    if allocations is None:
        allocations = order.allocations.select_related('stock__item','substitution')
    rows = [dict(requested_code=requested_code(a), quantity=a.quantity, substitute=hasattr(a,'substitution'))
            for a in allocations if a.state != 'cancelled']
    return picking_totals(order.payload.get('rows'), rows)
