export const internalAdId=(key:string)=>/(?:^id$|Id$|ID$|_id$|_ids$|Ids$)/.test(key.split('.').pop()||'')
export function adDisplayData(value:any):any {
 if(Array.isArray(value))return value.map(adDisplayData)
 if(value&&typeof value==='object')return Object.fromEntries(Object.entries(value).filter(([k])=>!internalAdId(k)).map(([k,v])=>[k,adDisplayData(v)]))
 return value
}
