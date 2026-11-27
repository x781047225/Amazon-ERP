export const destinationKey=(profile:string,id:string)=>profile+':'+id
export function destinationProfiles(profiles:any[],id:string){
 const selected=profiles.find(p=>p.id===id)
 const country=(v:string)=>v==='GB'?'UK':v
 return selected?profiles.filter(p=>p.account===selected.account&&country(p.country)===country(selected.country)&&p.currency===selected.currency):[{id}]
}
export function mergeDestinationOptions(sources:Map<string,any>){
 const campaigns:any[]=[],groups:any[]=[]
 for(const [profile_id,data] of sources){
  campaigns.push(...(data.campaigns||[]).map((c:any)=>({...c,entity_id:c.id,profile_id,id:destinationKey(profile_id,c.id)})))
  groups.push(...(data.groups||[]).map((g:any)=>({...g,entity_id:g.id,profile_id,id:destinationKey(profile_id,g.id),campaign_id:destinationKey(profile_id,g.campaign_id)})))
 }
 const time=(v:any)=>v.created_at?Date.parse(v.created_at):0
 campaigns.sort((a,b)=>(time(b)-time(a))||a.name.localeCompare(b.name)||a.id.localeCompare(b.id))
 groups.sort((a,b)=>(time(b)-time(a))||a.name.localeCompare(b.name)||a.id.localeCompare(b.id))
 return {campaigns,groups}
}
