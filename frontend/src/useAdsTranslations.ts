import {ref,watch} from 'vue'
import {api} from './session'
export type TermTranslation={translation?:string|null,status:string,retryAt?:number}
const values=ref<Record<string,TermTranslation>>({})
export const termText=(row:any)=>String(row.name||'').trim().replace(/\s+/g,' ')
export function translatableTerm(level:string,row:any){
 const text=termText(row)
 return ((level==='target'&&row.kind==='keyword')||(level==='negative'&&row.kind==='negative_keyword')||level==='search')&&!!text&&text.length<=300&&!/^(?:B0[A-Z0-9]{8}|[0-9]{9}[0-9X])$/i.test(text)&&!(/[\u3400-\u9fff]/.test(text)&&!/[A-Za-z]/.test(text))
}
export function useAdsTranslations(rows:()=>any[],level:()=>string,store:()=>string,active:()=>boolean){
 watch(()=>[rows(),level(),store(),active()],async(_v,_old,onCleanup)=>{
  let cancelled=false;onCleanup(()=>{cancelled=true})
  if(!active()||!store())return
  const terms=[...new Set(rows().filter(r=>translatableTerm(level(),r)).map(termText))].filter(t=>!values.value[t]||(values.value[t].status==='unavailable'&&Date.now()>Number(values.value[t].retryAt||0)))
  const currentStore=store()
  for(let i=0;i<terms.length;i+=8){
   if(cancelled)return
   const batch=terms.slice(i,i+8).filter(t=>values.value[t]?.status!=='loading'&&values.value[t]?.status!=='ready')
   if(!batch.length)continue
   for(const text of batch)values.value[text]={status:'loading'}
   try{
    const result=await api('/advertising/term-translations',{store:currentStore,terms:batch})
    for(const text of batch){const row=(result.rows||[]).find((r:any)=>r.text===text);values.value[text]={translation:row?.translation,status:row?.status||'unavailable',retryAt:Date.now()+60000}}
   }catch{for(const text of batch)values.value[text]={status:'unavailable',retryAt:Date.now()+60000}}
  }
 },{immediate:true})
 return (row:any)=>values.value[termText(row)]
}
