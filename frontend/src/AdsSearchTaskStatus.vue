<script setup lang="ts">
import {computed,onMounted,onUnmounted,watch} from 'vue'
import {session} from './session'
import {searchJobs,restoreSearchJobs,checkSearchJobs,dismissSearchJob,searchJobSummary} from './adsSearchJobs'
const pending=computed(()=>searchJobs.value.filter(j=>['sending','unconfirmed'].includes(j.status)).length)
const names:Record<string,string>={sending:'后台处理中',unconfirmed:'受理结果待核对',succeeded:'已完成',failed:'未全部完成',unknown:'结果待核对',queued:'排队中',ready:'待处理',exists:'已存在，跳过'}
const matches:Record<string,string>={EXACT:'精准',PHRASE:'词组',BROAD:'广泛'}
let timer:ReturnType<typeof setInterval>|undefined
watch(()=>session.value?.id,()=>{restoreSearchJobs();void checkSearchJobs()},{immediate:true})
onMounted(()=>{timer=setInterval(()=>void checkSearchJobs(),3000)})
onUnmounted(()=>clearInterval(timer))
</script>
<template><el-popover v-if="searchJobs.length" trigger="click" placement="bottom-end" :width="520">
<template #reference><el-button class="keyword-task-button" text aria-label="查看广告后台任务">广告任务<span v-if="pending"> · {{pending}}</span></el-button></template>
<div class="keyword-task-head"><b>广告任务</b><el-button link @click="checkSearchJobs">刷新结果</el-button></div>
<div class="keyword-tasks"><article v-for="job in [...searchJobs].reverse()" :key="job.id"><div class="keyword-task-head"><strong>{{job.label}}</strong><el-tag :type="job.status==='succeeded'?'success':job.status==='sending'?'info':'warning'">{{names[job.status]||job.status}}</el-tag></div><p>{{searchJobSummary(job)}}</p><p v-if="job.message">{{job.message}}</p><details><summary>查看逐项结果（{{job.items.length}}）</summary><div v-for="(item,i) in job.items" :key="i" class="keyword-task-item"><span>{{item.text}}<template v-if="job.type==='bulk'"> · {{item.currency}} {{item.before_value??'—'}} → {{item.after_value??'—'}}</template><template v-else-if="job.type!=='products'"> · {{matches[item.match]||item.match}} · {{item.payload?.bid??'—'}}</template></span><span>{{names[item.status]||item.status}} {{item.message||''}}</span></div></details><el-button v-if="job.status!=='sending'" link @click="dismissSearchJob(job.id)">移除此提示</el-button></article></div>
</el-popover></template>
<style scoped>.keyword-task-button{color:#dce9e4!important;background:transparent!important;margin-right:10px}.keyword-task-button:hover,.keyword-task-button:focus{background:#395267!important;color:#fff!important}.keyword-task-head{display:flex;justify-content:space-between;gap:12px;align-items:center}.keyword-task-head strong{overflow-wrap:anywhere}.keyword-tasks{max-height:60vh;overflow:auto}.keyword-tasks article{border-top:1px solid #e5ede8;padding:14px 0}.keyword-tasks p{font-size:12px;line-height:1.5}.keyword-tasks summary{cursor:pointer;color:#217660}.keyword-task-item{display:flex;flex-direction:column;gap:4px;padding:8px 0;border-bottom:1px solid #edf1ee;overflow-wrap:anywhere}</style>
