<script setup lang="ts">
import {computed,ref,watch} from 'vue'
import HomeDateRange from './HomeDateRange.vue'
import {useBusinessDay,financials,homeMoney as money,homePercent as percent,type HomeRange} from './homeFinancials'
const day=useBusinessDay()
const props=defineProps<{store:string,market:string,currency:string,refresh:number}>()
const data=ref<any>(null),busy=ref(false),error=ref(''),period=ref('7'),range=ref<HomeRange>({period:'7'}),bucket=ref('day'),hover=ref<number|null>(null)
let seq=0
async function load(force=false){const id=++seq;busy.value=true;error.value='';data.value=null;hover.value=null;try{const d=await financials(props.store,props.market,props.currency,range.value,force);if(id===seq)data.value=d}catch(e){if(id===seq)error.value=(e as Error).message}finally{if(id===seq)busy.value=false}}
function change(r:HomeRange){range.value=r;void load()}
const series=[{key:'sales',label:'销售额',color:'#63b98b'},{key:'estimated_profit',label:'订单利润',color:'#efaa62'},{key:'settlement_profit',label:'结算利润',color:'#a485d6'},{key:'gross_profit',label:'毛利润',color:'#299fea'}]
const lines=series.slice(0,3)
const margins=[['estimated','订单利润率'],['settlement','结算利润率'],['gross','毛利率']]
const points=computed<any[]>(()=>{
 const grouped=new Map<string,any>()
 for(const d of data.value?.points||[]){
  let key=d.label
  if(bucket.value==='month')key=key.slice(0,7)
  else if(bucket.value==='week'){const date=new Date(key+'T12:00:00Z');date.setUTCDate(date.getUTCDate()-(date.getUTCDay()+6)%7);key=date.toISOString().slice(0,10)}
  let p=grouped.get(key)
  if(!p){p={label:key,start:d.label,end:d.label,estimated_refunds:0,...Object.fromEntries(series.map(s=>[s.key,0]))};grouped.set(key,p)}
  p.start=p.start<d.label?p.start:d.label;p.end=p.end>d.label?p.end:d.label
  p.estimated_refunds+=Number(d.estimated_refunds||0)
  for(const {key:field} of series){if(d[field]==null)p[field]=null;else if(p[field]!=null)p[field]+=Number(d[field])}
 }
 return [...grouped.values()].map(p=>({...p,...Object.fromEntries(margins.map(([key])=>[key+'_margin',p.sales>0&&p[key+'_profit']!=null?p[key+'_profit']/p.sales*100:null]))}))
})
const limits=computed(()=>{const values=points.value.flatMap(p=>series.map(s=>p[s.key])).filter(v=>v!=null);return {min:Math.min(0,...values),max:Math.max(1,...values)}})
const px=(i:number)=>70+(i+.5)*760/Math.max(points.value.length,1)
const py=(v:number)=>265-(v-limits.value.min)/(limits.value.max-limits.value.min)*205
const barWidth=computed(()=>Math.min(40,500/Math.max(1,points.value.length)))
function path(key:string){let result='',active=false;points.value.forEach((p,i)=>{if(p[key]==null){active=false;return}result+=`${active?'L':'M'}${px(i)},${py(p[key])} `;active=true});return result}
const active=computed(()=>hover.value==null?null:points.value[hover.value])
function pointer(e:PointerEvent){if(!points.value.length)return;const r=(e.currentTarget as SVGElement).getBoundingClientRect(),x=(e.clientX-r.left)/r.width*920;hover.value=Math.min(points.value.length-1,Math.max(0,Math.floor((x-70)/760*points.value.length)))}
function keyboard(e:KeyboardEvent){if(points.value.length&&['ArrowRight','ArrowLeft'].includes(e.key)){e.preventDefault();hover.value=Math.max(0,Math.min(points.value.length-1,(hover.value??0)+(e.key==='ArrowRight'?1:-1)))}}
watch(()=>[props.store,props.market,props.currency,props.refresh],(v,old)=>void load(!!old&&v[3]!==old[3]),{immediate:true})
watch(bucket,()=>hover.value=null)
watch(day,()=>{if(range.value.period!=='custom')void load()})
</script>
<template><section class="panel profit-trend"><header><h2>销售额与利润走势</h2><HomeDateRange v-model="period" compact include-fortnight :start="data?.start" :end="data?.end" @change="change"><el-radio-group v-model="bucket" size="small" aria-label="趋势汇总维度"><el-radio-button value="day">日</el-radio-button><el-radio-button value="week">周</el-radio-button><el-radio-button value="month">月</el-radio-button></el-radio-group></HomeDateRange></header><el-alert v-if="error" :title="error" type="error" :closable="false"/><div v-loading="busy" class="profit-chart-wrap"><div class="profit-totals"><div v-for="s in series" :key="s.key" :title="s.key==='estimated_profit'?'预计回款 − 商品成本 − 预估头程运费':''"><span>{{s.label}}</span><strong>{{money(data?.summary[s.key],currency)}}<small v-if="s.key==='gross_profit'&&data?.summary.estimated_refunds" title="含退款暂估，悬浮图表查看说明">*</small></strong></div></div><div class="profit-legend"><span v-for="s in series" :key="s.key"><i :class="{bar:s.key==='gross_profit'}" :style="{background:s.color}"/>{{s.label}}</span></div><svg viewBox="0 0 920 315" class="profit-chart" role="img" aria-label="销售额、订单利润、结算利润折线与毛利润柱状图，左右方向键查看明细" tabindex="0" @pointermove="pointer" @pointerleave="hover=null" @focus="hover=points.length?0:null" @blur="hover=null" @keydown="keyboard"><text x="70" y="26">金额（{{currency}}）</text><g v-for="n in 5" :key="n"><line x1="70" x2="830" :y1="60+(n-1)*51.25" :y2="60+(n-1)*51.25" stroke="#e5eeeb" stroke-dasharray="4 4"/><text x="63" :y="64+(n-1)*51.25" text-anchor="end">{{Math.round(limits.max-(limits.max-limits.min)*(n-1)/4).toLocaleString()}}</text></g><line x1="70" x2="830" :y1="py(0)" :y2="py(0)" stroke="#aabcb2"/><g v-for="(p,i) in points" :key="p.label"><rect v-if="p.gross_profit!=null" class="gross-bar" :data-value="p.gross_profit" :x="px(i)-barWidth/2" :y="Math.min(py(p.gross_profit),py(0))" :width="barWidth" :height="Math.max(1,Math.abs(py(p.gross_profit)-py(0)))" rx="3" :fill="p.gross_profit<0?'#d66b75':'#299fea'"/><text v-if="i%Math.max(1,Math.ceil(points.length/7))===0" :x="px(i)" y="296" text-anchor="middle">{{bucket==='month'?p.label:p.label.slice(5)}}</text></g><template v-for="s in lines" :key="s.key"><path class="profit-line" :data-series="s.key" :d="path(s.key)" :stroke="s.color" fill="none" stroke-width="2.5"/><template v-for="(p,i) in points" :key="p.label"><circle v-if="p[s.key]!=null" :cx="px(i)" :cy="py(p[s.key])" r="3" :fill="s.color"/></template></template><line v-if="active&&hover!=null" :x1="px(hover)" :x2="px(hover)" y1="60" y2="265" stroke="#92a79d" stroke-dasharray="3 3"/></svg><div v-if="active" class="profit-tooltip" role="status"><b>{{active.start}}{{active.end!==active.start?' — '+active.end:''}}</b><template v-for="s in series" :key="s.key"><div>{{s.label}}：{{money(active[s.key],currency)}}</div><div v-if="s.key!=='sales'">{{margins.find(([key])=>s.key===key+'_profit')?.[1]}}：{{percent(active[s.key.replace('_profit','_margin')])}}</div></template><div v-if="active.estimated_refunds" class="profit-estimate-note">含 {{active.estimated_refunds}} 笔退款暂估：待核实部分按已退回可售，原回款缺失暂不加回；同步后重算。</div></div></div><span class="period-caption" :title="`订单利润为预计回款减商品成本、预估头程；结算按已释放入账日期。利润率均以同期订单销售额为分母；周/月按汇总金额计算。实际头程、退款口径与上方卡片一致；缺失金额显示—。`">{{data?.start}} — {{data?.end}} ⓘ</span></section></template>
<style scoped>
.profit-trend{padding:18px;min-width:0;margin:0}.profit-trend header{display:flex;gap:10px;align-items:stretch;flex-direction:column}.profit-trend h2{font-size:18px;line-height:26px;margin:0}.profit-totals{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;padding:20px 0 28px}.profit-totals span{display:block;font-size:13px;color:#84958c}.profit-totals strong{display:block;margin-top:12px;font-size:21px;overflow-wrap:anywhere}.profit-legend{display:flex;gap:18px;font-size:12px;flex-wrap:wrap}.profit-legend i{display:inline-block;width:16px;height:3px;margin-right:5px;vertical-align:middle}.profit-legend i.bar{width:10px;height:10px}.profit-chart{width:100%;min-height:240px}.profit-chart text{fill:#81938a;font-size:12px}.profit-chart-wrap{position:relative;min-height:380px}.profit-tooltip{position:absolute;right:20px;top:100px;background:#fff;box-shadow:0 4px 20px #23332925;border:1px solid #dae4de;border-radius:8px;padding:12px;font-size:12px;line-height:1.9;pointer-events:none}.profit-estimate-note{max-width:230px;white-space:normal;color:#8b7444;margin-top:5px}.period-caption{color:#8b9b92;font-size:11px;cursor:help}@media(max-width:650px){.profit-totals{grid-template-columns:repeat(2,minmax(0,1fr))}}
</style>
