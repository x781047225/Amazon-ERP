import {reactive} from 'vue'

// Shared across ad tabs: submitting one entity must not lock unrelated rows.
export const pendingAdEdits=reactive(new Map<string,{
 profile:string,kind:string,id:string,field:string,value:number|string,key:string,
 pendingStatus?:boolean,stage:'checking'|'sending'|'refreshing'
}>())
