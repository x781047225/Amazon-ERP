import {ref} from 'vue'
import {ElNotification} from 'element-plus'
import {session,api} from './session'
import type {Listing,PendingPrice} from './listingTypes'
type Job={id:string,pk:number,sku:string,target?:PendingPrice}
const key=()=>`erp-price-jobs:${session.value?.id}`
function read():Job[]{try{return JSON.parse(localStorage.getItem(key())||'[]')}catch{return []}}
const revision=ref(0)
function save(jobs:Job[]){localStorage.setItem(key(),JSON.stringify(jobs));revision.value++}
export function pendingPrice(row:Listing):PendingPrice|null{revision.value;return read().filter(j=>j.pk===row.id).at(-1)?.target||row.pending_price||null}
export function trackPriceJobs(rows:Listing[]){const jobs=read();for(const r of rows){if(r.pending_price&&!jobs.some(j=>j.id===r.pending_price!.id))jobs.push({id:r.pending_price.id,pk:r.id,sku:r.sku,target:r.pending_price})}save(jobs)}
function finished(job:Job,pricing?:unknown){window.dispatchEvent(new CustomEvent('erp:price-finished',{detail:{pk:job.pk,id:job.id,pricing}}))}
const notify=(sku:string,message:string)=>ElNotification({title:`调价未完成 · ${sku}`,message,type:'error',position:'top-right',duration:0})
export async function queuePrice(row:Listing,body:unknown,priceOnly=false){
 const bytes=crypto.getRandomValues(new Uint8Array(16));bytes[6]=(bytes[6]&15)|64;bytes[8]=(bytes[8]&63)|128;const hex=Array.from(bytes,b=>b.toString(16).padStart(2,'0')).join('');const id=[hex.slice(0,8),hex.slice(8,12),hex.slice(12,16),hex.slice(16,20),hex.slice(20)].join('-'),storageKey=key();const job:Job={id,pk:row.id,sku:row.sku,target:{...(priceOnly?row.pricing:{}),...(body as PendingPrice),price_only:priceOnly,id,status:'queued'}};save([...read(),job])
 try{await api(`/listings/${row.id}/${priceOnly?'price-only':'price'}/queue`,{...(body as object),request_id:id});return {ok:true,error:''}}
 catch(e){const jobs:Job[]=JSON.parse(localStorage.getItem(storageKey)||'[]');localStorage.setItem(storageKey,JSON.stringify(jobs.filter(j=>j.id!==id)));revision.value++;const error=(e as Error).message+'；如连接中断，请先核对提交记录。';if(!priceOnly)notify(row.sku,error);return {ok:false,error}}
}
let checking=false
export async function checkPriceJobs(){
 if(checking||!session.value)return;checking=true;const storageKey=key()
 try{for(const job of read()){
  try{const s=await api(`/listings/${job.pk}/price/jobs/${job.id}`);if(key()!==storageKey)return;if(['queued','submitting'].includes(s.status))continue
   if(!['accepted','verified','superseded'].includes(s.status))notify(job.sku,s.message||'请查看提交记录')
   save(read().filter(j=>j.id!==job.id));finished(job,s.pricing)
  }catch{/* Keep pending jobs across temporary connection failures. */}
 }}finally{checking=false}
}
