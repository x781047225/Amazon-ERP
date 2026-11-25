<script setup lang="ts">
import {computed,ref,watch} from 'vue'
import {homeMoney as money,homePercent as percent,homeColors as colors} from './homeFinancials'
type Part={label:string,amount:string,percent:string|null}
const props=defineProps<{rows:Part[],total:string|null,currency:string,label:string}>()
const selected=ref<number|null>(null)
const slices=computed(()=>{const total=props.rows.reduce((sum,row)=>sum+Math.abs(Number(row.amount)),0);let angle=-90;return props.rows.map((row,i)=>{const start=angle;angle+=total?Math.abs(Number(row.amount))/total*360:0;return {...row,color:colors[i%colors.length],start,end:angle}}).filter(s=>s.end>s.start)})
const active=computed(()=>selected.value==null?null:slices.value[selected.value])
const point=(r:number,a:number)=>`${100+r*Math.cos(a*Math.PI/180)},${100+r*Math.sin(a*Math.PI/180)}`
function arc(start:number,end:number){const mid=(start+end)/2;return `M${point(98,start)} A98,98 0 0 1 ${point(98,mid)} A98,98 0 0 1 ${point(98,end)} L${point(72,end)} A72,72 0 0 0 ${point(72,mid)} A72,72 0 0 0 ${point(72,start)} Z`}
watch(()=>props.rows,()=>selected.value=null)
</script>
<template><div class="composition-chart" @pointerleave="selected=null"><svg viewBox="0 0 200 200" :aria-label="label+'构成图'" role="group"><circle v-if="!slices.length" cx="100" cy="100" r="85" fill="none" stroke="#edf1ef" stroke-width="26"/><path v-for="(slice,i) in slices" :key="slice.label" :d="arc(slice.start,slice.end)" :fill="slice.color" :class="{active:selected===i}" tabindex="0" :aria-label="slice.label+'：'+money(slice.amount,currency)+'，'+percent(slice.percent)" @pointerenter="selected=i" @focus="selected=i" @blur="selected=null"/></svg><div class="composition-center"><strong>{{money(total,currency)}}</strong><span>{{label}}</span></div><div v-if="active" class="composition-tooltip" role="tooltip"><b>{{active.label}}</b><div>{{money(active.amount,currency)}}</div><small>占比 {{percent(active.percent)}}</small></div></div></template>
<style scoped>
.composition-chart{position:relative;width:155px;height:155px;margin:0 auto 15px}.composition-chart svg{width:100%;height:100%;overflow:visible}.composition-chart path{outline:none;cursor:default}.composition-chart path.active{filter:brightness(1.08);stroke:#fff;stroke-width:2}.composition-center{position:absolute;inset:35px 20px;display:flex;flex-direction:column;align-items:center;justify-content:center;pointer-events:none}.composition-center strong{font-size:16px;max-width:112px;overflow-wrap:anywhere;text-align:center}.composition-center span{font-size:12px;color:#81938a;margin-top:8px}.composition-tooltip{position:absolute;top:calc(100% - 12px);left:50%;transform:translateX(-50%);z-index:10;min-width:125px;max-width:240px;white-space:nowrap;background:#fff;border:1px solid #dce6e0;border-radius:7px;box-shadow:0 4px 18px #23332925;padding:9px 12px;font-size:12px;line-height:1.7;pointer-events:none}.composition-tooltip small{color:#81938a}
</style>
