"""Resolve owner fields from Amazon's read-only preparation requirements."""
from .sta_amazon import call,BASE,StaError
from .settings import MARKETS

def fetch_details(store,country,skus):
 details={}
 for pos in range(0,len(skus),100):
  # Requests applies normal URL encoding; these three characters require a second layer.
  encoded=[s.replace('%','%25').replace('+','%2B').replace(',','%2C') for s in skus[pos:pos+100]]
  response=call(store,'GET',BASE+'/items/prepDetails',params={'marketplaceId':MARKETS[country],'mskus':','.join(encoded)})
  for d in response.get('mskuPrepDetails',[]):
   if d.get('msku') in details:raise StaError('亚马逊返回重复的商品预处理要求，请稍后重试')
   details[d.get('msku')]=d
 if set(details)!=set(skus):raise StaError('亚马逊未返回全部商品的预处理要求，请稍后重试')
 return details

def choose_owner(constraint,sku):
 if constraint=='NONE_ONLY':return 'NONE'
 if constraint in (None,'SELLER_ONLY'):return 'SELLER'
 if constraint=='AMAZON_ONLY':raise StaError(f'{sku} 仅允许亚马逊处理，当前卖家发货方式不支持，请核对商品要求')
 raise StaError(f'{sku} 返回了未支持的处理要求，请核对后重试')

def resolve(store,country,items):
 details=fetch_details(store,country,[i['msku'] for i in items]);updated=[]
 for i in items:
  d=details[i['msku']];prep=choose_owner(d.get('prepOwnerConstraint'),i['msku']);label=choose_owner(d.get('labelOwnerConstraint'),i['msku'])
  if d.get('allOwnersConstraint') not in (None,'MUST_MATCH'):raise StaError(f"{i['msku']} 返回了未支持的负责人组合要求")
  if d.get('allOwnersConstraint')=='MUST_MATCH' and prep!=label:raise StaError(f"{i['msku']} 的贴标和预处理要求冲突，请核对商品资料")
  updated.append(dict(i,prepOwner=prep,labelOwner=label))
 return updated,details
