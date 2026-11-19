<script setup lang="ts">
import {computed,ref,watch,onUnmounted} from 'vue'
const props=defineProps<{change?:{before:unknown,after:unknown,changed_at:string},currency?:string,field?:string}>()
const visible=ref(false)
let expiryTimer:ReturnType<typeof setTimeout>|undefined
watch(()=>props.change,change=>{
 if(expiryTimer)clearTimeout(expiryTimer)
 const remaining=Date.parse(change?.changed_at||'')+24*60*60*1000-Date.now()
 visible.value=Number.isFinite(remaining)&&remaining>0
 if(visible.value)expiryTimer=setTimeout(()=>{visible.value=false},Math.min(remaining,2147483647))
},{immediate:true})
onUnmounted(()=>{if(expiryTimer)clearTimeout(expiryTimer)})
const time=computed(()=>props.change?new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23'}).format(new Date(props.change.changed_at)):'')
function value(v:unknown){
 if(v==null)return '未设置'
 if(props.field==='state')return ({ENABLED:'已开启',PAUSED:'已暂停',ARCHIVED:'已归档'} as Record<string,string>)[String(v)]||String(v)
 return typeof v==='number'?new Intl.NumberFormat('zh-CN',{minimumFractionDigits:2,maximumFractionDigits:2}).format(v):String(v)
}
</script>
<template>
 <el-tooltip v-if="visible&&change" placement="top">
  <template #content><div>最近修改：{{time}}（北京时间）</div><div>从 {{value(change.before)}} 修改为 {{value(change.after)}}</div></template>
  <span class="ads-recent-change" tabindex="0" aria-label="最近24小时内已修改">*</span>
 </el-tooltip>
</template>
<style scoped>
.ads-recent-change{display:inline-block;color:#e53935;font-size:20px;font-weight:700;line-height:1;cursor:help;flex-shrink:0;vertical-align:middle;margin-left:2px}
</style>
