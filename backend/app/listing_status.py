"""Conservative spreadsheet-facing status derived from marketplace-scoped evidence.

BUYABLE takes precedence over zero FBA stock (e.g. future supply offers).
Do not infer seller deletion from a missing page, failed sync or removed ASIN.
"""
from datetime import datetime, timezone
from .settings import MARKETS

LABELS={'on_sale':'在售','off_sale':'停售','deleted':'已删除','out_of_stock':'缺货','unknown':'未知'}


def mapped_status(row, inventory, at):
    data=row.listing_data or {}
    market=MARKETS[row.marketplace]
    summary=next((x for x in data.get('summaries',[]) if x.get('marketplaceId')==market),{})
    raw=summary.get('status')
    statuses=raw if isinstance(raw,list) else []
    issues=[x for x in data.get('issues',[]) if not x.get('marketplaceIds') or market in x['marketplaceIds']]
    actions=set()
    active_errors=[]
    for issue in issues:
        enforcement=issue.get('enforcements') or {}
        exemption=enforcement.get('exemption') or {}
        exempt=exemption.get('status')=='EXEMPT'
        if exemption.get('status')=='EXEMPT_UNTIL_EXPIRY_DATE':
            try:exempt=datetime.fromisoformat(exemption['expiryDate'].replace('Z','+00:00')).astimezone(timezone.utc)>at
            except (KeyError,ValueError,TypeError):exempt=True  # ambiguous exemption is not proof of a block
        if exempt:continue
        actions.update(x['action'] for x in enforcement.get('actions',[]) if isinstance(x,dict) and x.get('action'))
        if issue.get('severity')=='ERROR':active_errors.append(issue.get('code'))
    quantity=None
    quantity_source=None
    availability=data.get('fulfillmentAvailability') or []
    if availability:
        quantities=[]
        for item in availability:
            channel=item.get('fulfillmentChannelCode','')
            # Listings Items omits FBA quantity: use the independently synced,
            # identity-checked inventory, never the submitted attribute quantity.
            if channel.startswith('AMAZON_'):
                value=inventory.get('available') if not inventory.get('stale') and not inventory.get('error') else None
            else:value=item.get('quantity')
            quantities.append(value)
        if all(type(v) is int and v>=0 for v in quantities):
            quantity=sum(quantities)
            quantity_source='fulfillmentAvailability+inventory.available' if any(x.get('fulfillmentChannelCode','').startswith('AMAZON_') for x in availability) else 'fulfillmentAvailability'
    meta={'can_update':False,'reason':'','source':None,'as_of':row.synced_at,'inventory_as_of':inventory.get('synced_at'),'available_quantity':quantity,'raw_statuses':statuses,'issue_codes':[x.get('code') for x in issues if x.get('code')],'enforcement_actions':sorted(actions)}
    def result(code,reason,source=None):
        meta.update(can_update=code!='unknown',reason=reason,source=source)
        return {'listing_status':LABELS[code],'listing_status_code':code,'listing_status_detail':meta}
    if row.sync_error:
        return result('unknown','Listing 最近同步失败，保留原状态并重新同步。')
    if not isinstance(raw,list):
        return result('unknown','当前站点未返回 Listing 状态。')
    if 'DELETED' in statuses:
        if 'BUYABLE' in statuses:return result('unknown','原始状态存在冲突，需重新同步确认。')
        return result('deleted','亚马逊明确返回当前 SKU 已删除。','summaries.status.DELETED')
    if 'BUYABLE' in statuses:
        return result('on_sale','亚马逊确认当前站点可购买；不因可售库存为零改为缺货。','summaries.status.BUYABLE')
    parent=any(x.get('value')=='parent' and x.get('marketplace_id',market)==market for x in (row.attributes or {}).get('parentage_level',[]))
    parent=parent or any(r.get('childSkus') for x in data.get('relationships',[]) if x.get('marketplaceId',market)==market for r in x.get('relationships',[]))
    if parent:return result('unknown','父体不直接销售，不按缺货或停售更新。')
    blocked=actions & {'LISTING_SUPPRESSED','CATALOG_ITEM_REMOVED'}
    if blocked:
        return result('off_sale','当前不可购买，亚马逊存在停售限制；目录移除不等于卖家删除 SKU。','issues.enforcements.actions')
    if quantity==0 and not active_errors:
        return result('out_of_stock','当前不可购买，已确认所有配送渠道可售数量为零，未发现阻止销售的问题。',quantity_source)
    if quantity is not None and quantity>0:
        return result('off_sale','当前不可购买，但配送渠道仍有可售库存；具体原因以 Listing 问题为准。',quantity_source)
    return result('unknown','当前不可购买，但库存或问题证据不足，不能确认缺货、停售或已删除。')
