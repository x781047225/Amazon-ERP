import {onBeforeUnmount, onMounted, reactive} from 'vue'
import {session} from './session'

type Widths = Record<string, Record<string, number>>
const resetEvent = 'erp:reset-table-widths'
const validWidth = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value) && value >= 40 && value <= 3000

/** Widths are isolated by account, component and table (including nested/dialog tables). */
export function useTableWidths(scope: string) {
  const storageKey = `erp:table-widths:v1:${session.value?.username || 'guest'}:${scope}`
  const tables = reactive<Widths>({})
  try {
    const raw = localStorage.getItem(storageKey)
    const saved = raw === null && scope === 'ListingPanel'
      ? {'table-1': JSON.parse(localStorage.getItem('erp.listing.column-widths.v1') || '{}')}
      : JSON.parse(raw || '{}')
    for (const [table, values] of Object.entries(saved)) {
      if (!values || typeof values !== 'object' || Array.isArray(values)) continue
      tables[table] = Object.fromEntries(Object.entries(values).filter(([, width]) => validWidth(width)))
    }
  } catch { /* A blocked/corrupt storage entry must not prevent opening a list. */ }
  function persist() { try { localStorage.setItem(storageKey, JSON.stringify(tables)) } catch {} }
  function width(table: string, column: string, fallback?: number | string) { return tables[table]?.[column] ?? fallback }
  function resize(table: string, value: number, column: {columnKey?: string}) {
    if (!column.columnKey || !Number.isFinite(value)) return
    tables[table] = {...tables[table], [column.columnKey]: Math.max(40, Math.min(3000, Math.round(value)))}
    persist()
  }
  function reset(table: string) { tables[table] = {}; persist() }
  function resetColumn(table: string, column: {columnKey?: string}, event: MouseEvent) {
    if (!column.columnKey) return
    event.preventDefault()
    if (tables[table]) delete tables[table][column.columnKey]
    persist()
  }
  function resetVisible(event: Event) {
    const keys = (event as CustomEvent<string[]>).detail || []
    for (const table of Object.keys(tables)) if (keys.includes(`${scope}/${table}`)) reset(table)
  }
  onMounted(() => window.addEventListener(resetEvent, resetVisible))
  onBeforeUnmount(() => window.removeEventListener(resetEvent, resetVisible))
  return {tables, width, resize, reset, resetColumn}
}

export function resetVisibleTableWidths() {
  const keys = Array.from(document.querySelectorAll<HTMLElement>('[data-width-key]'))
    .filter(el => el.getClientRects().length > 0)
    .map(el => el.dataset.widthKey!)
  window.dispatchEvent(new CustomEvent(resetEvent, {detail: keys}))
}

export function resetTableWidths(key:string) { window.dispatchEvent(new CustomEvent(resetEvent,{detail:[key]})) }
