/** Shared alignment for tables mixing text and numeric columns. */
export function numericColumnAlign(label: unknown): 'center'|'left' {
 return /数量|件数|箱数|重量|尺寸|长|宽|高|单价|金额|净额|税率|税额|运费|清关费|报关费|附加费|杂费|合计|总计|总额|成本/.test(String(label||'')) ? 'center' : 'left'
}
