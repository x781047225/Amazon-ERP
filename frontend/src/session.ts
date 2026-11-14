import {ref} from 'vue'
export type User={id:number,username:string,is_admin:boolean,active:boolean,stores:string[],default_store:string,current_store:string,roles?:{id:number,name:string}[],permissions?:string[],warehouse_ids?:number[]}
export const session=ref<User|null>(null)
export async function api(path:string,body?:unknown){
 const r=await fetch('/api'+path,{method:body===undefined?'GET':'POST',headers:{'Content-Type':'application/json','X-ERP-Client':'web'},body:body===undefined?undefined:JSON.stringify(body)})
 const data=await r.json();if(!r.ok){if(r.status===401&&!path.startsWith('/auth/login'))window.dispatchEvent(new Event('erp:unauthorized'));throw new Error(typeof data.detail==='string'?data.detail:'请检查填写内容')};return data
}

export function can(key:string,action='view'){const u=session.value;return !!u&&(u.is_admin||!!u.permissions?.includes(key+'.'+action))}
export function firstAllowed(menu:string,tabs:string[]){return tabs.find(t=>can(menu+'.'+t))||''}
