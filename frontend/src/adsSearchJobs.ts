import {ref} from 'vue'
import {ElNotification} from 'element-plus'
import {api,session} from './session'
export type SearchJob={id:string,label:string,profile_id:string,status:string,message?:string,items:any[],notified?:boolean,type?:'bulk'|'products'}
export const searchJobs=ref<SearchJob[]>([])
const owner=()=>session.value?`erp-keyword-jobs:${session.value.id}`:''
const pending=(j:SearchJob)=>['sending','unconfirmed'].includes(j.status)
function read(key:string):SearchJob[]{try{const value=JSON.parse(localStorage.getItem(key)||'[]');return Array.isArray(value)?value.filter(j=>j&&typeof j.id==='string'&&Array.isArray(j.items)):[]}catch{return []}}
function save(key:string,jobs:SearchJob[]){const kept=jobs.filter(pending).concat(jobs.filter(j=>!pending(j)).slice(-15));try{localStorage.setItem(key,JSON.stringify(kept))}catch{}if(owner()===key)searchJobs.value=kept}
export function restoreSearchJobs(){searchJobs.value=owner()?read(owner()):[]}
export function dismissSearchJob(id:string){save(owner(),searchJobs.value.filter(j=>j.id!==id||j.status==='sending'))}
function patch(key:string,id:string,data:Partial<SearchJob>){const jobs=owner()===key?searchJobs.value:read(key);const job=jobs.find(j=>j.id===id);if(!job)return;if(data.items&&job.type==='bulk')data.items=data.items.map(i=>({...job.items.find(old=>old.id===i.id||(old.profile_id===i.profile_id&&old.kind===i.kind&&old.entity_id===i.entity_id)),...i}));if(data.items&&job.type==='products')data.items=data.items.map(i=>({...job.items.find(old=>old.text===i.text),...i}));Object.assign(job,data);save(key,[...jobs]);return job}
export function searchJobSummary(job:SearchJob){const n=(status:string)=>job.items.filter(i=>i.status===status).length;return `成功 ${n('succeeded')} · 已存在 ${n('exists')} · 失败 ${n('failed')} · 待核对 ${n('unknown')} · 待处理 ${n('queued')+n('ready')+n('sending')}`}
function finished(key:string,job:SearchJob){
 if(pending(job)||job.notified)return
 patch(key,job.id,{notified:true});if(owner()!==key)return
 ElNotification({title:job.type==='products'?(job.status==='succeeded'?'广告商品添加完成':'广告商品添加未全部完成'):job.type==='bulk'?(job.status==='succeeded'?'批量调整完成':'批量调整未全部完成'):(job.status==='succeeded'?'关键词添加完成':'关键词添加未全部完成'),message:job.label+'：'+searchJobSummary(job)+(job.message?'；'+job.message:''),type:job.status==='succeeded'?'success':'warning',duration:job.status==='succeeded'?5000:0,position:'top-right'})
 window.dispatchEvent(new CustomEvent('erp:keyword-job-finished',{detail:{profile_id:job.profile_id,items:job.type==='bulk'?job.items:undefined}}))
}
const submitting=new Set<string>()
export function submitSearchKeywords(body:any,label:string){
 const key=owner();if(!key)return
 const id=crypto.randomUUID(),job:SearchJob={id,label,profile_id:body.profile_id,status:'sending',items:body.items.map((i:any)=>({...i,status:'ready',payload:{bid:i.bid}}))}
 save(key,[...searchJobs.value,job]);submitting.add(id)
 ElNotification({title:'正在后台添加关键词',message:label+'，可继续操作；右上角“广告任务”可查看结果。',type:'info',duration:4000,position:'top-right'})
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
  try{const result=await api((job.type==='products'?'/advertising/create/status/':job.type==='bulk'?'/advertising/edit/bulk-status/':'/advertising/search-actions/status/')+job.id);if(owner()!==key)return;const saved=patch(key,job.id,result);if(saved)finished(key,saved)}catch{/* Retain the ID across temporary outages. */}
 }}finally{checking=false}
}

export function pendingBulkItem(profile:string,kind:string,id:string){
 return searchJobs.value.filter(j=>j.type==='bulk'&&pending(j)).flatMap(j=>j.items).find(i=>i.profile_id===profile&&i.kind===kind&&i.entity_id===id)
}
export function submitBulkEdits(rows:any[],label:string,archiving:boolean,directItems?:any[]){
 const key=owner();if(!key)return false
 const id=crypto.randomUUID(),job:SearchJob={id,label,type:'bulk',profile_id:rows[0].profile_id,status:'sending',items:rows.map((r,index)=>({id:r.previewId||id+':'+index,profile_id:r.profile_id,kind:r.kind||'campaign',entity_id:r.id,key:r.key,field:r.field,text:r.name,currency:r.currency,before_value:r.before,after_value:r.after,status:'queued'}))}
 save(key,[...searchJobs.value,job]);submitting.add(id)
 ElNotification({title:'正在提交后台任务',message:label+'，可继续操作；右上角“广告任务”可查看结果。',type:'info',duration:4000,position:'top-right'})
 void (async()=>{
  try{const result=await api(directItems?'/advertising/edit/bulk-bid-submit':'/advertising/edit/bulk-submit',directItems?{request_id:id,items:directItems}:{request_id:id,ids:rows.map(r=>r.previewId),acknowledge_archive:archiving});const saved=patch(key,id,result);if(saved)finished(key,saved)}
  catch(e){
   if([400,401,403,404,409,422].includes((e as any).httpStatus)){const saved=patch(key,id,{status:'failed',message:(e as Error).message,items:job.items.map(i=>({...i,status:'failed',message:'未受理'}))});if(saved)finished(key,saved);return}
   try{const result=await api('/advertising/edit/bulk-status/'+id);const saved=patch(key,id,result);if(saved)finished(key,saved)}
   catch{patch(key,id,{status:'unconfirmed',message:(e as Error).message+'；受理结果待核对，请勿重复提交。'});if(owner()===key)ElNotification({title:'批量调整受理结果待核对',message:'请在右上角广告任务查看结果，系统不会自动重复提交。',type:'warning',duration:0,position:'top-right'})}
  }finally{submitting.delete(id)}
 })()
 return true
}

export function submitAdProducts(body:any,label:string){
 const key=owner();if(!key)return false
 const skus=new Set(body.products.map((p:any)=>p.sku))
 if(searchJobs.value.some(j=>j.type==='products'&&['sending','unconfirmed','unknown'].includes(j.status)&&j.profile_id===body.profile_id&&j.items.some(i=>i.adgroup_id===body.adgroup_id&&skus.has(i.text)))){
  ElNotification({title:'商品已有待处理任务',message:'请先查看广告任务结果，勿重复添加。',type:'warning'});return false
 }
 const id=crypto.randomUUID(),job:SearchJob={id,label,type:'products',profile_id:body.profile_id,status:'sending',items:body.products.map((p:any)=>({text:p.sku,asin:p.asin,adgroup_id:body.adgroup_id,status:'queued'}))}
 save(key,[...searchJobs.value,job]);submitting.add(id)
 ElNotification({title:'正在后台添加广告商品',message:'可继续操作；右上角“广告任务”可查看结果。',type:'info',duration:4000})
 void (async()=>{
  try{const result=await api('/advertising/create/ad-submit',{...body,request_id:id});const saved=patch(key,id,result);if(saved)finished(key,saved)}
  catch(e){
   if([400,401,403,404,409,422].includes((e as any).httpStatus)){const saved=patch(key,id,{status:'failed',message:(e as Error).message,items:job.items.map(i=>({...i,status:'failed',message:'未受理'}))});if(saved)finished(key,saved);return}
   try{const result=await api('/advertising/create/status/'+id);const saved=patch(key,id,result);if(saved)finished(key,saved)}
   catch{patch(key,id,{status:'unconfirmed',message:(e as Error).message+'；受理结果待核对，请勿重复添加。'})}
  }finally{submitting.delete(id)}
 })()
 return true
}
