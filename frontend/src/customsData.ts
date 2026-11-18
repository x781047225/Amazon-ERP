export const sharedFields:Record<string,string>={brand_type:'品牌类型',brand:'品牌',material_cn:'中文材质',material_en:'英文材质',usage_cn:'中文用途',usage_en:'英文用途',origin:'原产地（地区）',domestic_source:'境内货源地',unit:'成交单位',model:'型号'}
export const defaults=()=>({currency:'CNY',origin:'中国',domestic_source:'深圳',model:'无型号',brand_type:'无品牌',brand:'无',export_preference:'不享惠',exemption:'照章征税'})
export const brands=['无品牌','境内自主品牌','境内收购品牌','境外品牌（贴牌生产）','境外品牌（其他）']
export function withDefaults(value:Record<string,any>={}){const out:Record<string,any>={...value};for(const [k,v] of Object.entries(defaults()))if(k!=='brand'&&!out[k])out[k]=v;if(!out.brand)out.brand=out.brand_type==='无品牌'?'无':'';return out}

import {reactive} from 'vue'
import {api} from './session'
export const vocabulary=reactive<Record<string,string[]>>({})
let vocabularyLoad:Promise<void>|undefined
export function loadVocabulary(){if(!vocabularyLoad)vocabularyLoad=api('/customs/choices').then(d=>{Object.assign(vocabulary,d)}).catch(e=>{vocabularyLoad=undefined;throw e});return vocabularyLoad}
