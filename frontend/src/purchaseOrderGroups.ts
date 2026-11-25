/** Group the API's complete document lines without changing the original editable records. */
export function groupPurchaseOrders(lines: Record<string, any>[]) {
  const documents = new Map<string, Record<string, any>[]>()
  for (const line of lines) {
    const key = String(line.number || `line:${line.id}`)
    if (!documents.has(key)) documents.set(key, [])
    documents.get(key)!.push(line)
  }
  const unique = (values: unknown[]) => [...new Set(values.filter(v => v !== null && v !== undefined && v !== '').map(String))]
  const sum = (items: Record<string, any>[], field: string) => items.reduce((total, item) => total + Number(item[field] || 0), 0)
  // Monetary API values have up to four decimal places. Sum at that precision.
  const amount = (items: Record<string, any>[], field: string) => items.reduce((total, item) => total + Math.round(Number(item[field] || 0) * 10000), 0) / 10000
  return [...documents].map(([number, items]) => {
    const quantity = sum(items, 'quantity'), planned = sum(items, 'planned_quantity')
    return {
      number, items, quantity, received: sum(items, 'received'),
      planned_quantity: planned,
      planning_status: planned >= quantity && quantity > 0 ? '已计划' : planned > 0 ? '部分已计划' : '',
      products:[...new Map(items.map(i=>[i.product_id,i])).values()],
      stores:unique(items.flatMap(i=>[i.store,...(i.allocations||[]).map((a:any)=>a.store)])),
      received_value:amount(items,'received_value'),goods_value: amount(items, 'goods_value'), extra_cost: amount(items, 'extra_cost'),
      suppliers: unique(items.map(i => i.supplier)), warehouses: unique(items.map(i => i.warehouse_name)),
      expected_dates: unique(items.map(i => i.expected_date)), statuses: unique(items.map(i => i.status)),
      plan_batch_codes: unique(items.flatMap(i => i.plan_batch_codes || [])),
    }
  })
}
