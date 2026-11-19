<script setup lang="ts">
import {ref,watch,onUnmounted} from 'vue'
import {api,can} from './session'
import {ElMessage,ElMessageBox} from 'element-plus'
const props=defineProps<{store:string}>()
const data=ref<any>({rows:[]}),enabled=ref(false),hour=ref(0),revision=ref(0),loaded=ref(false),saving=ref(false),error=ref('')
const labels:Record<string,string>={DE:'德国',FR:'法国',IT:'意大利',ES:'西班牙',SE:'瑞典',NL:'荷兰',PL:'波兰',BE:'比利时'}
const states:Record<string,string>={queued:'等待执行',checking:'查询预计金额',submitting:'正在申请',retry_wait:'等待重试',succeeded:'申请已受理',failed:'已停止',unknown:'结果待核对',skipped:'已跳过',cancelled:'已取消'}
let seq=0,disposed=false
const time=(s?:string)=>s?new Date(s).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23'}):'—'
async function load(reset=false){
 const id=++seq,s=props.store
 if(!s)return
 try{
  const d=await api('/settings/sync-data/payouts?store='+encodeURIComponent(s))
  if(disposed||id!==seq||s!==props.store)return
  data.value=d;error.value=''
  if(reset||!loaded.value){enabled.value=d.enabled;hour.value=d.hour;revision.value=d.revision;loaded.value=true}
 }catch(e){if(id===seq)error.value=(e as Error).message}
}
async function save(){
 const s=props.store,body={store:s,enabled:enabled.value,hour:hour.value,revision:revision.value}
 saving.value=true
 try{
  if(body.enabled){
   try{await ElMessageBox.confirm(s+' 将每天北京时间 '+body.hour+' 点，依次为德国、法国、意大利、西班牙、瑞典、荷兰、波兰、比利时申请提款至亚马逊已配置的默认收款账户。预计付款金额大于 0 才申请，实际能否提款由亚马逊判断。','开启定时提款',{confirmButtonText:'确认保存并开启',cancelButtonText:'取消',type:'warning'})}catch{return}
  }
  await api('/settings/sync-data/payouts/schedule',body)
  ElMessage.success('定时提款设置已保存')
  if(s===props.store)await load(true)
 }catch(e){ElMessage.error((e as Error).message)}finally{saving.value=false}
}
watch(()=>props.store,()=>{seq++;loaded.value=false;data.value={rows:[]};load(true)},{immediate:true})
const timer=setInterval(()=>{if(!saving.value)load()},5000)
onUnmounted(()=>{disposed=true;seq++;clearInterval(timer)})
</script>
<template>
<section class="payout-settings">
 <div class="payout-heading"><h3>定时提款</h3><el-button :disabled="saving" @click="load(true)">刷新提款设置</el-button></div>
 <p>每天按北京时间执行一次，支持德、法、意、西、瑞典、荷兰、波兰、比利时。<el-tag type="info">英国、爱尔兰：暂不支持</el-tag></p>
 <p>先查询预计付款金额，大于 0 才申请；预计金额不等于当前可提款余额，余额不足即停止。款项提至亚马逊配置的默认收款账户。</p>
 <div class="payout-controls">
  <el-switch v-model="enabled" aria-label="定时提款开关" active-text="开启" inactive-text="关闭" :disabled="!loaded||saving||!can('settings.sync-data','operate')"/>
  <el-select v-model="hour" aria-label="提款时间（北京时间）" :disabled="!loaded||saving||!can('settings.sync-data','operate')" style="width:220px"><el-option v-for="h in 24" :key="h-1" :value="h-1" :label="'每天 '+(h-1)+' 点（北京时间）'"/></el-select>
  <el-button v-if="can('settings.sync-data','operate')" :loading="saving" :disabled="!loaded" @click="save">保存提款设置</el-button>
 </div>
 <p>已保存：{{data.enabled?'每天 '+data.hour+' 点':'未开启'}} · 下次触发：{{time(data.next_run_at)}}<span v-if="loaded&&(enabled!==data.enabled||hour!==data.hour)">（有未保存的修改）</span></p>
 <p>各站点间隔至少 24 小时；仅因间隔未满失败时，每 10 分钟重试，最多追加 3 次。其他失败停止本轮；结果不明时暂停该站点，需人工核对。关闭后取消尚未执行的任务和重试，已发出的申请不能撤回。</p>
 <el-alert v-if="error" :title="error" type="warning" :closable="false"/>
 <el-table :data="data.rows" row-key="id" max-height="420" border empty-text="暂无提款记录（默认关闭）">
  <el-table-column label="计划执行时间（北京时间）" width="220"><template #default="{row}"><template v-if="row.scheduled_at">{{time(row.scheduled_at)}}</template><template v-else>{{row.day}}<div class="time-note">历史记录未保存计划时分秒</div></template></template></el-table-column>
  <el-table-column label="最近开始时间（北京时间）" width="220"><template #default="{row}">{{time(row.started_at)}}<div v-if="!row.started_at&&row.attempts>0" class="time-note">历史记录未保存开始时间</div></template></el-table-column>
  <el-table-column label="状态更新时间（北京时间）" width="220"><template #default="{row}">{{time(row.updated_at)}}</template></el-table-column>
  <el-table-column label="站点" width="100"><template #default="{row}">{{labels[row.marketplace]||row.marketplace}}</template></el-table-column>
  <el-table-column label="状态" width="145"><template #default="{row}"><el-tag :type="row.status==='succeeded'?'success':['failed','unknown'].includes(row.status)?'warning':'info'">{{states[row.status]||row.status}}</el-tag></template></el-table-column>
  <el-table-column label="预计付款金额" width="170"><template #default="{row}">{{row.amount==null?'—':row.amount+' '+(row.currency||'')}}</template></el-table-column>
  <el-table-column label="尝试次数" width="100" prop="attempts"/>
  <el-table-column label="下次重试时间（北京时间）" width="220"><template #default="{row}">{{row.status==='retry_wait'?time(row.next_attempt_at):'—'}}</template></el-table-column>
  <el-table-column prop="message" label="处理结果" min-width="280"/>
  <el-table-column prop="reference" label="提款申请编号" min-width="200"/>
 </el-table>
 <p>显示最近 80 条记录。所有时间均为北京时间，精确到秒；最近开始时间是最近一次尝试的开始时间。每个店铺、站点每天只创建一条任务，当天已有任务时，修改执行时刻不会再执行一轮。申请已受理不代表已到账。</p>
</section>
</template>
<style scoped>
.time-note{font-size:12px;color:#71847b;margin-top:4px}.payout-settings{margin:28px 0;padding:20px;background:white;border:1px solid #dfe7e3;border-radius:10px}.payout-heading{display:flex;justify-content:space-between;align-items:center}.payout-heading h3{margin:0}.payout-controls{display:flex;gap:16px;align-items:center;flex-wrap:wrap}.payout-settings p{font-size:13px;color:#71847b;line-height:1.8}
</style>
