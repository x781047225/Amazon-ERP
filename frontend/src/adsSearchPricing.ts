// CPC belongs to the selected report period and source currency, not the target group.
export function actualCpc(rows:any[],currency:string,profiles:any[]){
 if(!rows.length||!currency)return null
 let cost=0,clicks=0
 for(const row of rows){
  const sourceCurrency=row.currency||profiles.find(p=>p.id===row.profile_id)?.currency
  if(sourceCurrency!==currency)return null
  const m=row.metrics||{},n=Number(m.clicks),amount=Number(m.cost)
  if(m.clicks==null||m.cost==null||!Number.isFinite(n)||!Number.isFinite(amount)||n<0||amount<0)return null
  cost+=amount;clicks+=n
 }
 return clicks>0&&cost>0?cost/clicks:null
}
