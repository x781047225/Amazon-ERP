<script setup lang="ts">
import {useTableWidths} from './tableWidths'
const tableWidths=useTableWidths('FinanceSummaryPanel')

import {ref,onMounted,onUnmounted,watch} from 'vue'
import {ElMessage} from 'element-plus'
import {api} from './session'
import {displayCurrency} from './currency'
const props=defineProps<{store:string}>()
const store=ref(props.store),market=ref(''),currency=ref(displayCurrency.value),days=ref(1),stores=ref<string[]>([]),markets=ref<string[]>([]),jobs=ref<any[]>([]),busy=ref(false),error=ref(''),result=ref<any>(null),resultJob=ref<any>(null),opened=ref(false)
const names:Record<string,string>={UK:'英国',DE:'德国',FR:'法国',ES:'西班牙',IT:'意大利',NL:'荷兰',BE:'比利时',PL:'波兰',SE:'瑞典',IE:'爱尔兰',AE:'阿联酋',SA:'沙特'}
const states:Record<string,string>={queued:'下载中 · 排队中',running:'下载中',succeeded:'已完成',failed:'下载失败'}
const time=(v:string)=>v?new Date(v).toLocaleString('zh-CN'):'—'
const money=(v:any,c:string)=>v==null?'未返回数据':new Intl.NumberFormat('zh-CN',{style:'currency',currency:c}).format(Number(v))
const day=(d:Date)=>`${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`
let timer:ReturnType<typeof setInterval>|undefined,seq=0,disposed=false
async function load(){const id=++seq;try{const data=await api('/finance/summary/jobs?'+new URLSearchParams({store:store.value}));if(id===seq&&!disposed){jobs.value=data;error.value=''}}catch(e){if(id===seq&&!disposed)error.value=(e as Error).message}}
async function generate(){const today=new Date(),dates=[day(new Date(today.getFullYear(),today.getMonth(),today.getDate()-days.value+1)),day(today)];busy.value=true;try{await api('/finance/summary/jobs',{store:store.value,marketplace:market.value,start:dates[0],end:dates[1],currency:currency.value});await load();ElMessage.success('已加入后台下载，可稍后在下载记录中领取结果')}catch(e){ElMessage.error((e as Error).message)}finally{busy.value=false}}
async function retry(j:any){try{await api(`/finance/summary/jobs/${j.id}/retry`,{});await load()}catch(e){ElMessage.error((e as Error).message)}}
async function preview(j:any){try{const d=await api(`/finance/summary/jobs/${j.id}`);result.value=d.result;resultJob.value=d;opened.value=true}catch(e){ElMessage.error((e as Error).message)}}
onMounted(async()=>{try{const o=await api('/finance/summary/options');stores.value=o.stores;markets.value=o.markets;if(!stores.value.includes(store.value))store.value=stores.value[0]||'';await load();if(!disposed)timer=setInterval(load,5000)}catch(e){error.value=(e as Error).message}})
onUnmounted(()=>{disposed=true;seq++;clearInterval(timer)})
watch(()=>props.store,v=>{if(stores.value.includes(v))store.value=v})
watch(store,()=>{opened.value=false;load()})
</script>
<template>
<section class="panel financial-summary">
  <h3>按日期范围获取财务汇总</h3>
  <form class="summary-filters" @submit.prevent="generate">
    <el-select v-model="store" aria-label="财务汇总店铺" placeholder="选择店铺"><el-option v-for="s in stores" :key="s" :label="s" :value="s"/></el-select>
    <el-select v-model="market" aria-label="财务汇总站点" placeholder="全部站点" clearable><el-option label="全部站点" value=""/><el-option v-for="m in markets" :key="m" :label="`${names[m]||m} · ${m}`" :value="m"/></el-select>
    <el-select v-model="days" aria-label="财务汇总日期范围"><el-option v-for="d in [1,3,7,14,30,90,180,365]" :key="d" :value="d" :label="`最近${d}天`"/></el-select>
    <el-select v-model="currency" aria-label="财务汇总币种"><el-option label="人民币 CNY" value="CNY"/><el-option label="欧元 EUR" value="EUR"/><el-option label="英镑 GBP" value="GBP"/></el-select>
    <el-button type="primary" native-type="submit" :loading="busy" :disabled="!store">下载财务汇总</el-button>
  <TableReset table-key="FinanceSummaryPanel/table-1"/></form>
  <p class="metrics-note">按各站点当地日期统计财务活动。全部站点会换算为所选币种，采用生成时最新可用参考汇率；文件保留原币金额和汇率日期。</p>
  <p class="metrics-note">生成 Excel 后可查看汇总和下载结果。关闭页面不影响后台任务；财务净额不等于利润或银行到账。</p>
</section>
<el-alert v-if="error" :title="error" type="error" :closable="false"/>
<section class="panel financial-summary summary-history">
  <div class="summary-heading"><h3>下载记录</h3><el-button @click="load">刷新记录</el-button></div>
  <template v-if="true"><el-table border data-width-key="FinanceSummaryPanel/table-1" @header-dragend="(width: number, _old: number, column: any) => tableWidths.resize('table-1', width, column)" @header-contextmenu="(column: any, event: MouseEvent) => tableWidths.resetColumn('table-1', column, event)" :data="jobs">
    <el-table-column :column-key="'column-1'" :width="tableWidths.width('table-1', 'column-1', undefined)" label="创建时间" min-width="170"><template #default="{row}">{{time(row.created_at)}}</template></el-table-column>
    <el-table-column :column-key="'store'" :width="tableWidths.width('table-1', 'store', undefined)" prop="store" label="店铺" min-width="110"/>
    <el-table-column :column-key="'column-3'" :width="tableWidths.width('table-1', 'column-3', undefined)" label="站点" min-width="110"><template #default="{row}">{{row.marketplace?(names[row.marketplace]||row.marketplace):'全部站点'}}</template></el-table-column>
    <el-table-column :column-key="'column-4'" :width="tableWidths.width('table-1', 'column-4', undefined)" label="日期范围" min-width="225"><template #default="{row}">{{row.start_date}} 至 {{row.end_date}}</template></el-table-column>
    <el-table-column :column-key="'currency'" prop="currency" label="汇总币种" :width="tableWidths.width('table-1', 'currency', '100')"/>
    <el-table-column :column-key="'column-6'" :width="tableWidths.width('table-1', 'column-6', undefined)" label="状态" min-width="160"><template #default="{row}"><el-tag :type="row.status==='failed'?'danger':row.status==='succeeded'?'success':'warning'">{{states[row.status]||row.status}}</el-tag><div v-if="row.status==='running'" class="cell-sub">已读取 {{row.pages}} 页</div></template></el-table-column>
    <el-table-column :column-key="'error'" :width="tableWidths.width('table-1', 'error', undefined)" prop="error" label="说明" min-width="240"/>
    <el-table-column :column-key="'column-8'" :width="tableWidths.width('table-1', 'column-8', undefined)" label="操作" min-width="185" fixed="right"><template #default="{row}"><template v-if="row.status==='succeeded'"><el-button link type="primary" @click="preview(row)">查看汇总</el-button><a :href="`/api/finance/summary/jobs/${row.id}/download`" class="summary-download">下载结果</a></template><el-button v-else-if="row.status==='failed'" link type="primary" @click="retry(row)">重试</el-button><span v-else class="muted">可稍后回来下载</span></template></el-table-column>
    <template #empty><el-empty description="选择日期范围，点击下载财务汇总"/></template>
  </el-table></template>
</section>
<el-dialog v-model="opened" title="财务汇总" width="1000px">
 <template v-if="result&&resultJob">
   <p>{{resultJob.store}} · {{resultJob.start_date}} 至 {{resultJob.end_date}} · {{result.currency}}</p>
   <h2>财务净额 {{money(result.net_proceeds,result.currency)}}</h2>
   <p class="metrics-note">汇率日期 {{result.fx.date||'同币种无需换算'}} · 财务项目上下级存在包含关系，不重复累加。</p>
   <el-alert v-if="!result.count" title="亚马逊未返回该范围的汇总数据，不代表金额为零。" type="info" :closable="false"/>
   <template v-if="true"><TableReset table-key="FinanceSummaryPanel/table-2"/><el-table border data-width-key="FinanceSummaryPanel/table-2" @header-dragend="(width: number, _old: number, column: any) => tableWidths.resize('table-2', width, column)" @header-contextmenu="(column: any, event: MouseEvent) => tableWidths.resetColumn('table-2', column, event)" :data="result.totals" max-height="380"><el-table-column :column-key="'path'" :width="tableWidths.width('table-2', 'path', undefined)" prop="path" label="财务项目" min-width="400"/><el-table-column :column-key="'column-2'" label="金额" :width="tableWidths.width('table-2', 'column-2', '180')" align="center"><template #default="{row}">{{money(row.amount,result.currency)}}</template></el-table-column></el-table></template>
   <h3>各站点原币及换算金额</h3><template v-if="true"><TableReset table-key="FinanceSummaryPanel/table-3"/><el-table border data-width-key="FinanceSummaryPanel/table-3" @header-dragend="(width: number, _old: number, column: any) => tableWidths.resize('table-3', width, column)" @header-contextmenu="(column: any, event: MouseEvent) => tableWidths.resetColumn('table-3', column, event)" :data="result.sites" max-height="280"><el-table-column :column-key="'marketplace'" :width="tableWidths.width('table-3', 'marketplace', undefined)" prop="marketplace" label="站点"/><el-table-column :column-key="'account'" :width="tableWidths.width('table-3', 'account', undefined)" prop="account" label="账户类型"/><el-table-column :column-key="'column-3'" :width="tableWidths.width('table-3', 'column-3', undefined)" label="原币净额" align="center"><template #default="{row}">{{money(row.original,row.original_currency)}}</template></el-table-column><el-table-column :column-key="'column-4'" :width="tableWidths.width('table-3', 'column-4', undefined)" :label="`换算净额（${result.currency}）`" align="center"><template #default="{row}">{{money(row.amount,result.currency)}}</template></el-table-column></el-table></template>
 </template>
</el-dialog>
</template>
<style scoped>
.financial-summary{padding:22px;margin-bottom:20px}.financial-summary h3{margin:0 0 18px}.summary-filters{display:flex;gap:12px;align-items:center;flex-wrap:wrap}.summary-filters>.el-select{width:170px}.summary-filters :deep(.el-date-editor){width:300px;flex-grow:0}.summary-heading{display:flex;justify-content:space-between;align-items:center}.summary-download{color:#217563;font-weight:600;margin-left:12px;text-decoration:none}.summary-history{padding-bottom:12px}
</style>
