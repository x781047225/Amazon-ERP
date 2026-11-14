<script setup lang="ts">
import {ref,watch,onUnmounted,computed} from 'vue'
import {api} from './session'
const props=defineProps<{order:any|null}>(),emit=defineEmits<{close:[]}>()
const rows=ref<any[]>([]),loading=ref(false),error=ref('')
let generation=0
const timers=new Map<number,ReturnType<typeof setTimeout>>()
function cancelTimers(){for(const timer of timers.values())clearTimeout(timer);timers.clear()}
watch(()=>props.order,async order=>{
 const g=++generation;cancelTimers();rows.value=[];error.value='';loading.value=false;if(!order)return
 loading.value=true
 try{const data=await api(`/orders/${order.id}/income-items`);if(g!==generation)return
  rows.value=data.items.map((r:any)=>({...r,price:r.unit_price||'',currency:r.display_currency||r.currency||({UK:'GBP',PL:'PLN',SE:'SEK'} as Record<string,string>)[order.marketplace]||'EUR',result:null,busy:false,problem:'',version:0}))
  loading.value=false
  // Start one SKU at a time to avoid a burst of Amazon fee requests.
  for(const row of rows.value){if(g!==generation)return;if(!row.error&&row.price&&row.version===0)await calculate(row,g)}
 }catch(e){if(g===generation)error.value=(e as Error).message}finally{if(g===generation)loading.value=false}
})
onUnmounted(()=>{generation++;cancelTimers()})
function close(){generation++;cancelTimers();emit('close')}
function changed(row:any){row.version++;row.result=null;row.problem='';row.busy=false;clearTimeout(timers.get(row.id));const g=generation;timers.set(row.id,setTimeout(()=>calculate(row,g),600))}
async function calculate(row:any,g=generation){
 clearTimeout(timers.get(row.id));timers.delete(row.id)
 const version=++row.version;row.result=null;row.problem=''
 if(!/^\d+(\.\d{1,2})?$/.test(row.price)||Number(row.price)<=0){row.problem='请输入大于 0、最多两位小数的单价';return}
 const id=props.order?.id;if(!id||row.error||g!==generation)return
 row.busy=true
 try{const data=await api(`/orders/${id}/income-items/${row.id}/estimate`,{price:row.price,currency:row.currency});if(g!==generation||version!==row.version)return;row.result=data}
 catch(e){if(g===generation&&version===row.version)row.problem=(e as Error).message}
 finally{if(g===generation&&version===row.version)row.busy=false}
}
const complete=computed(()=>rows.value.length>0&&rows.value.every(r=>r.result&&!r.busy&&!r.problem&&!r.error))
const totals=computed(()=>{if(!complete.value)return [];const groups:Record<string,{currency:string,revenue:number,profit:number}>={};for(const row of rows.value){const c=row.result.unit.currency;const group=groups[c]??={currency:c,revenue:0,profit:0};group.revenue+=Math.round(Number(row.result.totals.price)*100);group.profit+=Math.round(Number(row.result.totals.gross_profit)*100)}return Object.values(groups)})
const fmt=(v:any)=>v==null?'—':Number(v).toFixed(2)
const breakdown={referral_fee:'销售佣金',fba_fee:'FBA费用',other_amazon_fees:'其他亚马逊费用',vat:'增值税预估',purchase_cost:'采购成本',freight_cost:'头程运费',gross_profit:'预计毛利'}
</script>
<template><el-dialog :model-value="!!order" title="订单毛利测算" width="min(960px,95vw)" :close-on-click-modal="false" @close="close">
<p>{{order?.order_id}} · {{order?.store}} · {{order?.marketplace}}</p><p class="calc-note">按商品分别测算；修改单价后自动重新计算，仅用于本次测算，不修改订单和亚马逊售价。</p>
<el-alert v-if="error" :title="error" type="error" :closable="false"/><div v-if="loading">正在读取订单商品…</div><el-empty v-else-if="!rows.length&&!error" description="订单商品尚未同步"/>
<section v-for="row in rows" :key="row.id" class="sku-calculation"><b>{{row.sku}}</b><p class="calc-note">{{row.title}}</p>
<div class="price-row"><span>数量 {{row.quantity}}</span><el-input v-model="row.price" :aria-label="row.sku+'测算单价'" :disabled="!!row.error" placeholder="单价" @input="changed(row)"><template #prepend>单价</template><template #append>{{row.currency}}</template></el-input><el-button :disabled="!!row.error" :loading="row.busy" @click="calculate(row)">重新计算</el-button></div>
<el-alert v-if="row.error||row.problem" :title="row.error||row.problem" type="warning" :closable="false"/>
<div v-if="row.busy" class="calc-note">正在计算此 SKU…</div>
<template v-if="row.result"><div class="cost-grid cost-head"><span>费用明细</span><span>单件（{{row.currency}}）</span><span>{{row.quantity}} 件合计（{{row.currency}}）</span></div><div v-for="(label,key) in breakdown" :key="key" class="cost-grid" :class="{profit:key==='gross_profit'}"><span>{{label}}</span><span>{{fmt(row.result.unit[key])}}</span><span>{{fmt(row.result.totals[key])}}</span></div><p class="calc-note">预计毛利率 {{row.result.unit.gross_margin}}% · 增值税：含税售价 ÷ 1.20 × 20%</p><p v-if="row.result.unit.purchase_missing||row.result.unit.freight_missing" class="cost-warning">采购成本或头程资料不完整，缺失项按 0 测算，毛利可能偏高。</p></template>
</section>
<div class="order-calc-total"><template v-if="complete"><div v-for="t in totals" :key="t.currency">测算收入 {{t.currency}} {{fmt(t.revenue/100)}} · <b>预计毛利 {{t.currency}} {{fmt(t.profit/100)}}</b> · 毛利率 {{t.revenue?fmt(t.profit/t.revenue*100):'—'}}%</div></template><span v-else>全部 SKU 计算完成后显示订单合计</span></div>
<template #footer><el-button @click="close">关闭</el-button></template></el-dialog></template>
<style scoped>.sku-calculation{padding:18px 0;border-bottom:1px solid #e4eae6}.calc-note{color:#819087;font-size:12px;line-height:1.8}.price-row{display:flex;gap:16px;align-items:center;margin:14px 0}.price-row .el-input{max-width:330px}.cost-grid{display:grid;grid-template-columns:1.3fr 1fr 1fr;gap:12px;padding:8px 12px;border-bottom:1px solid #f0f3f1}.cost-grid span:not(:first-child){text-align:right}.cost-head{background:#f6f8f7;color:#809087}.profit,.order-calc-total{color:#226b55;background:#edf6f0}.order-calc-total{padding:16px;margin-top:16px}.cost-warning{color:#ad772e;font-size:12px}</style>
