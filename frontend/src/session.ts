import {ref} from 'vue'
export type User={id:number,username:string,is_admin:boolean,active:boolean,stores:string[],default_store:string,current_store:string,roles?:{id:number,name:string}[],permissions?:string[],warehouse_ids?:number[]}
export const session=ref<User|null>(null)
export async function api(path:string,body?:unknown,options?:{signal?:AbortSignal}){
 let r:Response
 try{r=await fetch('/api'+path,{method:body===undefined?'GET':'POST',headers:{'Content-Type':'application/json','X-ERP-Client':'web'},body:body===undefined?undefined:JSON.stringify(body),signal:options?.signal})}
 catch{throw new Error('暂时无法连接服务器，请检查网络后刷新。')}
 if(r.status===401&&!path.startsWith('/auth/login'))window.dispatchEvent(new Event('erp:unauthorized'))
 let data:any
 try{data=await r.json()}
 catch{throw new Error(r.status===401?'登录已过期，请重新登录。':`服务器暂未返回有效数据（HTTP ${r.status}），请稍后刷新；如持续出现，请检查服务和反向代理配置。`)}
 if(!r.ok)throw Object.assign(new Error(typeof data?.detail==='string'?data.detail:`请求未完成（HTTP ${r.status}），请稍后重试。`),{httpStatus:r.status})
 return data
}

export function can(key:string,action='view'){const u=session.value;return !!u&&(u.is_admin||!!u.permissions?.includes(key+'.'+action)||(key==='products'&&action==='view'&&['settings.clearance.view','settings.declaration.view'].some(p=>u.permissions?.includes(p))))}
export function firstAllowed(menu:string,tabs:string[]){return tabs.find(t=>can(menu+'.'+t))||''}
