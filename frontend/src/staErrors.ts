const addressNames:Record<string,string>={state:'州/省',stateOrProvinceCode:'州/省',city:'城市',country:'国家代码',countryCode:'国家代码',postalCode:'邮编',zip:'邮编',addressLine1:'街道地址',addressLine2:'地址补充',name:'联系人',phoneNumber:'联系电话',email:'邮箱',companyName:'公司名称'}
const addressKeys:Record<string,string>={state:'stateOrProvinceCode',country:'countryCode',zip:'postalCode'}
export function staErrorInfo(raw:string){
 const fields:Record<string,string>={}
 for(const match of raw.matchAll(/\b(stateOrProvinceCode|state|city|countryCode|country|postalCode|zip|addressLine1|addressLine2|phoneNumber|email|companyName|name)\s*:\s*([^\];\n]+)/gi)){
  const key=Object.keys(addressNames).find(k=>k.toLowerCase()===match[1].toLowerCase())!
  const reason=match[2],label=addressNames[key],missing=/missing|required|empty|blank/i.test(reason)
  fields[addressKeys[key]||key]=missing?'请补充'+label:label+'未通过地址校验，请核对'
 }
 if(Object.keys(fields).length)return {title:'发货地址未通过校验：'+Object.values(fields).join('；')+'。修改后再提交。',type:'warning' as const,fields}
 let title=raw,type:'warning'|'error'='error'
 if(/provided address is invalid|invalid address/i.test(raw)){title='发货地址未通过亚马逊校验，请核对街道、城市、州/省、国家代码和邮编是否一致。';type='warning'}
 else if(/prepOwner/i.test(raw)){title='商品预处理责任方设置不符合亚马逊要求，请检查下方提示的商品及预处理设置。';type='warning'}
 else if(/labelOwner/i.test(raw)){title='商品贴标责任方设置不符合亚马逊要求，请检查商品的贴标设置。';type='warning'}
 else if(/HTTP\s*429|throttl|quota exceeded/i.test(raw)){title='亚马逊请求过于频繁，请稍后刷新进度再操作。';type='warning'}
 else if(/HTTP\s*(401|403)|unauthorized|access denied/i.test(raw))title='当前店铺授权或操作权限不足，请检查店铺授权与账号权限。'
 else if(/HTTP\s*409/i.test(raw)){title='货件状态已发生变化，请刷新本地进度后再操作。';type='warning'}
 else if(/HTTP\s*(400|422)/i.test(raw)){title='提交资料未通过校验，请核对本步骤的必填项、格式及商品信息；具体字段可查看技术详情。';type='warning'}
 else if(/HTTP\s*5\d\d/i.test(raw))title='亚马逊服务暂时异常，请先核对货件是否已创建或更新，避免重复提交。'
 else if(!/[\u4e00-\u9fff]/.test(raw))title='本步骤未完成，请查看技术详情中的原因；结果不明确时先核对亚马逊货件状态。'
 return {title,type,fields}
}
