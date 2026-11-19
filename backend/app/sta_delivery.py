"""Shared validation for choosing delivery windows before requesting carriers."""
from datetime import date, datetime, timezone, timedelta, time
from zoneinfo import ZoneInfo

SHIPPING_ZONE=ZoneInfo("Asia/Shanghai")
from .sta_amazon import StaError

def shipment_ids(t):
 ids=[s['shipmentId'] for s in t.result.get('shipments',[]) if s.get('placementOptionId')==t.result.get('placementConfirmed')]
 if not ids:raise StaError('请先确认分仓方案并获取收货仓库')
 return ids

def timestamp(value):
 try:
  parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
  if parsed.tzinfo is None:raise ValueError()
  return parsed.astimezone(timezone.utc)
 except (TypeError,AttributeError,ValueError):raise StaError('送达时段日期不完整，请重新获取可选送达时段') from None

def validate_windows(t, selections, ready_date=None, optional_ids=()):
 ids=shipment_ids(t)
 if not isinstance(selections,list) or any(not isinstance(x,dict) for x in selections) or len(selections)!=len(ids) or {x.get('shipmentId') for x in selections}!=set(ids):
  raise StaError('请为每个收货仓填写完整送达时段，再获取承运人')
 selected={}
 for x in selections:
  sid=x['shipmentId'];wid=x.get('deliveryWindowOptionId')
  if not wid and sid in optional_ids:continue
  win=next((w for w in t.result.get('deliveryWindows',{}).get(sid,[]) if w.get('deliveryWindowOptionId')==wid),None) if wid else None
  if not win:raise StaError('请为每个收货仓选择有效送达时段；时段失效时请重新获取')
  start,end=timestamp(win.get('startDate')),timestamp(win.get('endDate'))
  if end<=start or end<=datetime.now(timezone.utc) or win.get('availabilityType')=='BLOCKED':raise StaError('所选送达时段不可用，请重新选择')
  if win.get('validUntil') and timestamp(win['validUntil'])<=datetime.now(timezone.utc):raise StaError('送达时段已过期，请重新获取')
  if ready_date and end.date()<ready_date:raise StaError('送达时段不能早于计划发货日期')
  selected[sid]=wid
 return selected

def validate_ready_date(p):
 try:ready=date.fromisoformat(p.get('readyDate',''))
 except (TypeError,ValueError):raise StaError('请填写计划发货日期') from None
 if ready<datetime.now(SHIPPING_ZONE).date():raise StaError('计划发货日期不能早于今天')
 return ready

def validate_transport_request(t,p):
 ready=validate_ready_date(p)
 selected=validate_windows(t,p.get('selections'),ready,optional_ids=shipment_ids(t))
 return ready,selected

def can_edit_delivery(t):
 return (t.stage=='delivery' and not t.result.get('transportConfirmed') and
  (t.status=='ready' or (t.status=='failed' and (t.command.get('action') in ('transport_options','delivery_windows') or (t.command.get('action')=='transport' and not t.journal)) and
   all(v.get('state') in ('done','failed') for v in t.journal.values()))))


def ready_to_ship_start(ready, current=None):
 """Interpret shipping dates in ERP time; same-day pickup must remain in the future."""
 current=(current or datetime.now(timezone.utc)).astimezone(timezone.utc)
 today=current.astimezone(SHIPPING_ZONE).date()
 if ready<today:raise StaError('计划发货日期不能早于今天')
 start=datetime.combine(ready,time.min,tzinfo=SHIPPING_ZONE).astimezone(timezone.utc)
 # Amazon truncates seconds; round UP and leave time for request/operation processing.
 earliest=(current+timedelta(minutes=11)).replace(second=0,microsecond=0)
 if start<earliest:start=earliest
 return start.strftime('%Y-%m-%dT%H:%M:%SZ')


def requires_window(option):
 return option.get('shippingSolution')!='AMAZON_PARTNERED_CARRIER' or 'CONFIRMED_DELIVERY_WINDOW' in option.get('preconditions',[])

def validate_quote(option):
 if option.get('shippingSolution')!='AMAZON_PARTNERED_CARRIER':return
 from math import isfinite
 quote=option.get('quote') or {};cost=quote.get('cost') or {}
 try:valid=isfinite(float(cost['amount'])) and float(cost['amount'])>=0 and bool(cost.get('code'))
 except (ValueError,TypeError,KeyError):valid=False
 if not valid:raise StaError('亚马逊合作承运人尚未返回有效运费，请重新获取报价后再确认')
 if quote.get('expiration'):
  try:expired=timestamp(quote['expiration'])<=datetime.now(timezone.utc)
  except StaError:expired=True
  if expired:raise StaError('合作承运人报价已过期，请重新获取报价后再确认')
