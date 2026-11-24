"""FBA return evidence and refund/sale pairing; reads never call Amazon."""
import csv
import io
from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal
from sqlalchemy import select
from .models import FinanceTransaction, now
from .fee_reports import FeeReport
from .amazon import AmazonError

REPORT_TYPE = 'GET_FBA_FULFILLMENT_CUSTOMER_RETURNS_DATA'


def parse_report(raw):
    reader = csv.DictReader(io.StringIO(raw), delimiter='\t')
    required = {'return-date', 'order-id', 'sku', 'quantity', 'detailed-disposition', 'status'}
    if not required <= set(reader.fieldnames or []):
        raise AmazonError('FBA退货报告缺少必要字段，保留上次记录')
    result = []
    for row in reader:
        try:
            qty = int(row['quantity'])
            date = datetime.fromisoformat(row['return-date'].replace('Z', '+00:00'))
            if qty <= 0 or date.tzinfo is None or not row['order-id'] or not row['sku']:
                raise ValueError()
        except (ValueError, TypeError):
            raise AmazonError('FBA退货报告日期或数量无效，保留上次记录') from None
        # Deliberately omit customer comments and product titles.
        result.append({key: row.get(key, '').strip() for key in
                       ('return-date', 'order-id', 'sku', 'asin', 'fnsku',
                        'fulfillment-center-id', 'detailed-disposition', 'status', 'license-plate-number')} | {'quantity': qty})
    return result


def identifiers(row):
    return {x.get('relatedIdentifierName'): x.get('relatedIdentifierValue')
            for x in row.data.get('relatedIdentifiers', [])}


def canonical(rows):
    """Release replaces its deferred snapshot; business ids deduplicate snapshots."""
    replaced = {identifiers(r).get('DEFERRED_TRANSACTION_ID') for r in rows
                if r.status in ('RELEASED', 'DEFERRED_RELEASED')}
    groups = {}
    for row in rows:
        if row.transaction_id in replaced:
            continue
        ids = identifiers(row)
        key = (row.transaction_type.lower(), ids.get('REFUND_ID') or ids.get('SHIPMENT_ID') or row.transaction_id)
        rank = ({'RELEASED': 3, 'DEFERRED_RELEASED': 2, 'DEFERRED': 1}.get(row.status, 0), row.posted_at)
        if key not in groups or rank > groups[key][0]:
            groups[key] = rank, row
    return [r for _, r in groups.values()]


def product_items(row, fx):
    from .finance_records import amount
    out = []
    raw = row.data.get('items', [])
    for item in raw:
        contexts = [c for c in item.get('contexts', []) if c.get('sku')]
        if len(contexts) != 1:
            return []
        c = contexts[0]; qty = c.get('quantityShipped')
        if isinstance(qty, bool) or not isinstance(qty, (int, float)) or qty <= 0 or int(qty) != qty:
            return []
        ids = {i.get('itemRelatedIdentifierName'): i.get('itemRelatedIdentifierValue')
               for i in item.get('relatedIdentifiers', [])}
        net = amount(item.get('totalAmount'), fx)
        if net is None and len(raw) == 1:
            net = amount({'currencyAmount': row.amount, 'currencyCode': row.currency}, fx)
        out.append({'sku': c['sku'], 'quantity': int(qty), 'net': net,
                    'item_id': ids.get('ORDER_ADJUSTMENT_ITEM_ID'), 'network': c.get('fulfillmentNetwork')})
    return out


def refund_contexts(db, store, order_ids, fx):
    """Allocate original sale net once per refunded unit, independent of page/date filters."""
    from .finance_records import CODES, amount
    rows = list(db.scalars(select(FinanceTransaction).where(
        FinanceTransaction.store == store, FinanceTransaction.order_id.in_(order_ids)))) if order_ids else []
    orders = defaultdict(list)
    for r in rows:
        orders[r.order_id].append(r)
    snapshot = db.get(FeeReport, (store, 'returns'))
    evidence = snapshot.rows[0] if snapshot and snapshot.rows and snapshot.rows[0].get('schema') == 'fba-returns-v1' else {}
    try:
        start = datetime.fromisoformat(evidence['start'])
        end = datetime.fromisoformat(evidence['end'])
        fresh = snapshot.synced_at and now() - snapshot.synced_at <= timedelta(hours=36)
    except (KeyError, ValueError, TypeError):
        start = end = None; fresh = False
    returned = defaultdict(list)
    for item in evidence.get('items', []):
        returned[(item['order-id'], item['sku'])].append(item)
    results = {}
    for order_id, group in orders.items():
        unique = canonical(group)
        sales = [r for r in unique if r.transaction_type.lower() in {c.lower() for c in CODES['sales']}]
        refunds = sorted([r for r in unique if r.status == 'RELEASED' and r.transaction_type.lower() in {c.lower() for c in CODES['refund']}], key=lambda r: (r.posted_at, r.id))
        sale_items = [(r, item) for r in sales for item in product_items(r, fx)]
        refund_items = {r.id: product_items(r, fx) for r in refunds}
        total_qty = defaultdict(int)
        for items in refund_items.values():
            for item in items:
                total_qty[item['sku']] += item['quantity']
        used_returns = defaultdict(int)
        used_sales = defaultdict(int)
        for refund in refunds:
            notes = []; base = Decimal(0); loss = []; statuses = []; ready = True
            items = refund_items[refund.id]
            if not items:
                ready = False; notes.append('退款流水商品及数量不完整，毛利润待核实')
            for item in items:
                matches = [(r, i) for r, i in sale_items if i['sku'] == item['sku'] and r.marketplace == refund.marketplace]
                exact = [(r, i) for r, i in matches if item['item_id'] and i['item_id'] == item['item_id']]
                if exact:
                    matches = exact
                # Ambiguous split shipments or refunds exceeding shipped quantity must not reuse sale money.
                if len(matches) != 1 or matches[0][1]['net'] is None or total_qty[item['sku']] > matches[0][1]['quantity']:
                    ready = False; notes.append('原销售回款无法唯一关联或累计退款数量超出销售数量，毛利润待核实'); continue
                source, sold = matches[0]
                sale_key = (source.id, sold['item_id'] or sold['sku'])
                allocated = used_sales[sale_key]
                unit_base = Decimal(sold['net']) / Decimal(sold['quantity'])
                base += (unit_base * (allocated + item['quantity'])).quantize(Decimal('.01')) - (unit_base * allocated).quantize(Decimal('.01'))
                used_sales[sale_key] += item['quantity']
                notes.append('关联原销售回款（'+{'DEFERRED':'暂缓','RELEASED':'已释放','DEFERRED_RELEASED':'暂缓历史'}.get(source.status,source.status)+'），按退款商品数量分摊')
                if not fresh or start is None or source.posted_at < start or source.posted_at > end or refund.posted_at > end or (item['network'] or sold['network']) != 'AFN':
                    ready = False; statuses.append('待核实'); notes.append('退货报告未覆盖订单、尚未同步、已过期或非FBA订单，退回状态待核实'); continue
                records = returned[(order_id,item['sku'])]
                sellable = 0; damaged = 0; unknown = 0
                for record in records:
                    disposition = record['detailed-disposition'].upper().replace('_',' ')
                    if disposition == 'SELLABLE': sellable += record['quantity']
                    elif disposition in ('CUSTOMER DAMAGED','DEFECTIVE','DAMAGED','CARRIER DAMAGED','WAREHOUSE DAMAGED','DISTRIBUTOR DAMAGED','EXPIRED'): damaged += record['quantity']
                    else: unknown += record['quantity']
                qty = item['quantity']; before = used_returns[item['sku']]; used_returns[item['sku']] += qty
                if unknown or (sellable and damaged and qty < sellable+damaged):
                    ready = False; statuses.append('待核实'); notes.append('部分退回商品可售状态或退款对应关系不明确，毛利润待核实'); continue
                recovered = max(0, min(qty, sellable-before))
                lost = qty-recovered
                if recovered: statuses.append('已退回可售')
                if lost:
                    unsellable = max(0, min(before+qty, sellable+damaged)-max(before, sellable))
                    if unsellable: statuses.append('已退回不可售')
                    if lost > unsellable: statuses.append('未查到退回记录')
                    loss.append({'sku':item['sku'],'quantity':lost})
                if lost > 0 and not records:notes.append('截至退货报告覆盖时间未查到退回记录，按未退回暂估；后续退回后自动重算')
                notes.append(f"{item['sku']}：退款{qty}件，退回可售{recovered}件，计入成本损失{lost}件")
            results[refund.id] = {'original_sale_net': str(base.quantize(Decimal('.01'))) if items and not any('原销售回款无法' in n for n in notes) else None,
                'ready': ready, 'loss_items': loss, 'return_status': ' / '.join(dict.fromkeys(statuses)) or '待核实',
                'notes': notes, 'return_checked_at': snapshot.synced_at if snapshot else None}
    return results
