import {api} from './session'
import {ref,onMounted,onUnmounted} from 'vue'
export const businessDay=()=>new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date())
export function useBusinessDay(){
 const day=ref(businessDay());let timer:ReturnType<typeof setInterval>|undefined
 const update=()=>{day.value=businessDay()}
 onMounted(()=>{timer=setInterval(update,30000);window.addEventListener('focus',update)})
 onUnmounted(()=>{clearInterval(timer);window.removeEventListener('focus',update)})
 return day
}
export type HomeRange={period:string,start?:string,end?:string}
const pending=new Map<string,Promise<any>>()
const cached=new Map<string,{at:number,data:any}>()
export async function financials(store:string,market:string,currency:string,range:HomeRange,refresh=false){
 const params=new URLSearchParams({store,marketplace:market||'',currency,...range}),key=params.toString()+'|'+(range.period==='custom'?'fixed':businessDay())
 if(refresh)params.set('refresh','true')
 if(!refresh&&cached.has(key)&&Date.now()-cached.get(key)!.at<60000)return cached.get(key)!.data
 const requestKey=key+(refresh?'|refresh':'')
 if(pending.has(requestKey))return pending.get(requestKey)
 const promise=api('/dashboard/financials?'+params).then(data=>{cached.set(key,{at:Date.now(),data});if(cached.size>32)cached.delete(cached.keys().next().value!);return data}).finally(()=>pending.delete(requestKey))
 pending.set(requestKey,promise);return promise
}
export const homeMoney=(v:any,c:string)=>v==null?'—':new Intl.NumberFormat('zh-CN',{style:'currency',currency:c}).format(Number(v))
export const homePercent=(v:any)=>v==null?'—':Number(v).toLocaleString('zh-CN',{maximumFractionDigits:2})+'%'
export const homeColors=['#48b7f3','#ff86a4','#98d769','#bd80f0','#ffce45','#ffa575','#50d7da','#b9abf4','#56ac35']
