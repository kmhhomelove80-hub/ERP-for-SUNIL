"""수동 수량·삭제 처리를 원본과 분리해 보관합니다."""
import hashlib,json,math
from collections import defaultdict
from .engine import product_key

def quantity(value):
 try:q=float(value)
 except (ValueError,TypeError):raise ValueError('수량은 0 이상의 숫자여야 합니다.')
 if not math.isfinite(q) or q<0:raise ValueError('수량은 0 이상의 유한한 숫자여야 합니다.')
 return int(q) if q.is_integer() else q

def apply_manual(orders,manual):
 counts=defaultdict(int);out=[]
 for order in orders:
  values=order['values'][:]
  signature=hashlib.sha256(json.dumps([order['yard'],*[values[i] for i in (0,1,2,3,10,11)]],ensure_ascii=False,default=str).encode()).hexdigest()
  key=order.get('rowkey') or f"{order['yard']}:{signature}:{counts[signature]}";counts[signature]+=1
  edit=manual.get(key,{})
  if edit.get('deleted'):continue
  if 'quantity' in edit:values[4]=quantity(edit['quantity'])
  out.append({**order,'values':values,'rowkey':key})
 totals=defaultdict(float)
 for row in out:totals[(row['yard'],product_key(row['values']))]+=row['values'][4] or 0
 for row in out:
  total=totals[(row['yard'],product_key(row['values']))];row['values'][6]=int(total) if total.is_integer() else total
 return out
