<script setup lang="ts">
import {useTableWidths} from './tableWidths'
const tableWidths=useTableWidths('ListingArrivalDialog')

import type {Listing} from './listingTypes'
defineProps<{row:Listing|null}>()
const emit=defineEmits<{close:[]}>()
const states:Record<string,string>={scheduled:'已计入对应期限',later:'预计在 60 天以后到达',unknown:'缺少有效窗口或读取失败',overdue:'窗口已逾期，等待更新',unverified:'站点尚未核实'}
const statusNames:Record<string,string>={SHIPPED:'已发货',IN_TRANSIT:'运输中'}
const date=(s:string|null,zone:string)=>s?new Date(s).toLocaleString('zh-CN',{timeZone:zone,hour12:false}):'未返回'
</script>
<template>
<el-dialog :model-value="!!row" title="预计到达明细" width="min(1340px, 96vw)" @close="emit('close')">
<template v-if="row"><p class="arrival-sku">{{row.sku}} · {{row.store}} · {{row.marketplace}}</p>
<el-alert v-if="row.arrivals.error" :title="row.arrivals.error" type="warning" :closable="false"/>
<p>按送达窗口的<strong>结束日期</strong>统计未来 15／30／60 天内累计到仓件数（含今天），三个数字不能相加。送达窗口是预期安排，不代表实际到货或上架日期。</p>
<p class="arrival-summary">缺日期／读取失败 {{row.arrivals.unknown}} 件 · 站点待核实 {{row.arrivals.unverified}} 件 · 窗口逾期 {{row.arrivals.overdue}} 件 · 60 天以后 {{row.arrivals.later}} 件。这些数量未计入三个期限。</p>
<template v-if="true"><TableReset table-key="ListingArrivalDialog/table-1"/><el-table data-width-key="ListingArrivalDialog/table-1" @header-dragend="(width: number, _old: number, column: any) => tableWidths.resize('table-1', width, column)" @header-contextmenu="(column: any, event: MouseEvent) => tableWidths.resetColumn('table-1', column, event)" :data="row.arrivals.details" border max-height="440">
<el-table-column :column-key="'shipment_id'" :width="tableWidths.width('table-1', 'shipment_id', undefined)" prop="shipment_id" label="FBA 货件" min-width="150"/>
<el-table-column column-key="dispatched_on" label="发货日期" width="120"><template #default="{row:item}">{{item.dispatched_on||'—'}}</template></el-table-column>
<el-table-column column-key="logistics_channel" label="物流渠道" min-width="160"><template #default="{row:item}">{{item.logistics_channel||'—'}}</template></el-table-column>
<el-table-column :column-key="'units'" prop="units" label="剩余件数" :width="tableWidths.width('table-1', 'units', '90')" align="center"/>
<el-table-column :column-key="'column-3'" :width="tableWidths.width('table-1', 'column-3', undefined)" label="状态 / 仓库" min-width="110"><template #default="{row:item}">{{statusNames[item.status]||item.status}}<div>{{item.warehouse||'—'}}</div></template></el-table-column>
<el-table-column :column-key="'column-4'" :width="tableWidths.width('table-1', 'column-4', undefined)" label="预计送达窗口" min-width="205"><template #default="{row:item}">{{date(item.start,row.arrivals.timezone)}}<div>至 {{date(item.end,row.arrivals.timezone)}}</div></template></el-table-column>
<el-table-column :column-key="'column-5'" :width="tableWidths.width('table-1', 'column-5', undefined)" label="统计说明" min-width="180"><template #default="{row:item}">{{states[item.bucket]}}<div class="muted" v-if="item.error">{{item.error}}</div></template></el-table-column>
<template #empty><el-empty description="当前缓存中没有可匹配的运输在途货件；可同步到达时间并在 FBA 中心核对货件"/></template>
</el-table></template>
<p class="arrival-foot">时间按 {{row.arrivals.timezone}} 展示。上次扫描：{{date(row.arrivals.synced_at,row.arrivals.timezone)}}{{row.arrivals.stale?'（缓存待刷新）':''}}。仅关联本地已导入货件、同店铺 SKU＋FNSKU 及该站点入库计划；送达、接收中和未发货货件不计入。欧洲共享库存可能与本站点计划数量不同，请勿与库存栏相加。</p>
</template>
<template #footer><el-button @click="emit('close')">关闭</el-button></template>
</el-dialog>
</template>
<style scoped>
p{font-size:12px;line-height:1.8;color:#667d70}.arrival-sku{font-weight:600;color:#285f49}.arrival-summary{background:#f5f8f6;padding:8px 12px}.arrival-foot{font-size:11px;color:#8a9b91}
</style>
