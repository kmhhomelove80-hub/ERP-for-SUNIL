"""Small immutable descriptions captured at transaction boundaries."""
def stock_details(stock):
    return dict(code=stock.item.code,model=stock.legacy_name or stock.item.name,specification=stock.legacy_specification or stock.item.specification,location=stock.location,warehouse=stock.warehouse,status=stock.status)
