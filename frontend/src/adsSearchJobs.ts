import {ref} from 'vue'
import {ElNotification} from 'element-plus'
import {api,session} from './session'
export type SearchJob={id:string,label:string,profile_id:string,status:string,message?:string,items:any[],notified?:boolean}
export const searchJobs=ref<SearchJob[]>([])
const owner=()=>session.value?`erp-keyword-jobs:${session.value.id}`:''
const pending=(j:SearchJob)=>['sending','unconfirmed'].includes(j.status)
function read(key:string):SearchJob[]{try{const value=JSON.parse(localStorage.getItem(key)||'[]');return Array.isArray(value)?value.filter(j=>j&&typeof j.id==='string'&&Array.isArray(j.items)):[]}catch{return []}}
function save(key:string,jobs:SearchJob[]){const kept=jobs.filter(pending).concat(jobs.filter(j=>!pending(j)).slice(-15));try{localStorage.setItem(key,JSON.stringify(kept))}catch{}if(owner()===key)searchJobs.value=kept}
export function restoreSearchJobs(){searchJobs.value=owner()?read(owner()):[]}
export function dismissSearchJob(id:string){save(owner(),searchJobs.value.filter(j=>j.id!==id||j.status==='sending'))}
function patch(key:string,id:string,data:Partial<SearchJob>){const jobs=owner()===key?searchJobs.value:read(key);const job=jobs.find(j=>j.id===id);if(!job)return;Object.assign(job,data);save(key,[...jobs]);return job}
export function searchJobSummary(job:SearchJob){const n=(status:string)=>job.items.filter(i=>i.status===status).length;return `成功 ${n('succeeded')} · 已存在 ${n('exists')} · 失败 ${n('failed')} · 待核对 ${n('unknown')}`}
function finished(key:string,job:SearchJob){
 if(pending(job)||job.notified)return
 patch(key,job.id,{notified:true});if(owner()!==key)return
 ElNotification({title:job.status==='succeeded'?'关键词添加完成':'关键词添加未全部完成',message:job.label+'：'+searchJobSummary(job)+(job.message?'；'+job.message:''),type:job.status==='succeeded'?'success':'warning',duration:job.status==='succeeded'?5000:0,position:'top-right'})
 window.dispatchEvent(new CustomEvent('erp:keyword-job-finished',{detail:{profile_id:job.profile_id}}))
}
const submitting=new Set<string>()
export function submitSearchKeywords(body:any,label:string){
 const key=owner();if(!key)return
 const id=crypto.randomUUID(),job:SearchJob={id,label,profile_id:body.profile_id,status:'sending',items:body.items.map((i:any)=>({...i,status:'ready',payload:{bid:i.bid}}))}
 save(key,[...searchJobs.value,job]);submitting.add(id)
 ElNotification({title:'正在后台添加关键词',message:label+'，可继续操作；右上角“关键词任务”可查看结果。',type:'info',duration:4000,position:'top-right'})
 void (async()=>{
  try{const result=await api('/advertising/search-actions/submit',{...body,request_id:id});const saved=patch(key,id,result);if(saved)finished(key,saved)}
  catch(e){
   if([400,401,403,404,409,422].includes((e as any).httpStatus)){const saved=patch(key,id,{status:'failed',message:(e as Error).message,items:job.items.map(i=>({...i,status:'failed',message:'未受理'}))});if(saved)finished(key,saved);return}
   // A lost response does not mean the write failed. Never automatically resubmit.
   try{const result=await api('/advertising/search-actions/status/'+id);const saved=patch(key,id,result);if(saved)finished(key,saved)}
   catch{patch(key,id,{status:'unconfirmed',message:(e as Error).message+'；尚未确认受理，请核对任务结果，勿重复添加。'});if(owner()===key)ElNotification({title:'关键词提交待核对',message:label+'：'+(e as Error).message+'；请查看右上角任务结果。',type:'warning',duration:0,position:'top-right'})}
  }finally{submitting.delete(id)}
 })()
}
let checking=false
export async function checkSearchJobs(){
 if(checking||!owner())return;checking=true;const key=owner()
 try{for(const job of [...searchJobs.value].filter(pending)){
  if(submitting.has(job.id))continue
  try{const result=await api('/advertising/search-actions/status/'+job.id);if(owner()!==key)return;const saved=patch(key,job.id,result);if(saved)finished(key,saved)}catch{/* Retain the ID across temporary outages. */}
 }}finally{checking=false}
}
