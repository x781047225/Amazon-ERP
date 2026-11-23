import {ref} from 'vue'
export type User={id:number,username:string,is_admin:boolean,active:boolean,stores:string[],default_store:string,current_store:string,roles?:{id:number,name:string}[],permissions?:string[],warehouse_ids?:number[]}
export const session=ref<User|null>(null)
export async function api(path:string,body?:unknown,options?:{signal?:AbortSignal}){
 let r:Response
 try{r=await fetch('/api'+path,{method:body===undefined?'GET':'POST',headers:{'Content-Type':'application/json','X-ERP-Client':'web'},body:body===undefined?undefined:JSON.stringify(body),signal:options?.signal})}
 catch{throw Object.assign(new Error('网络连接中断，请检查网络连接。'),{transient:true})}
 if(r.status===401&&!path.startsWith('/auth/login'))window.dispatchEvent(new Event('erp:unauthorized'))
 const serverMessages:Record<number,string>={500:'服务器暂时无法完成请求。',502:'服务连接暂时中断。',503:'服务暂时繁忙。',504:'服务器响应超时。'}
 if(r.status>=500)throw Object.assign(new Error(serverMessages[r.status]||'服务暂时不可用。'),{httpStatus:r.status,transient:true})
 let data:any
 try{data=await r.json()}
 catch{throw Object.assign(new Error(r.status===401?'登录已过期，请重新登录。':'服务器返回的数据不完整，暂时无法确认处理结果。'),{httpStatus:r.status,transient:r.ok})}
 if(!r.ok)throw Object.assign(new Error(typeof data?.detail==='string'?data.detail:`请求未完成（HTTP ${r.status}），请稍后重试。`),{httpStatus:r.status})
 return data
}

export function can(key:string,action='view'){const u=session.value;return !!u&&(u.is_admin||!!u.permissions?.includes(key+'.'+action)||(key==='products'&&action==='view'&&['settings.clearance.view','settings.declaration.view'].some(p=>u.permissions?.includes(p))))}
export function firstAllowed(menu:string,tabs:string[]){return tabs.find(t=>can(menu+'.'+t))||''}
