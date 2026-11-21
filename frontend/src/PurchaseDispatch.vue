<script setup lang="ts">
import {ref,computed} from 'vue'
import {ElMessage} from 'element-plus'
import {api} from './session'
import {useTableWidths} from './tableWidths'
const widths=useTableWidths('PurchaseDispatch'),props=defineProps<{stores:string[]}>(),emit=defineEmits<{completed:[]}>()
const visible=ref(false),busy=ref(false),error=ref(''),rows=ref<any[]>([]),selection=ref<any>({}),lock=ref(false),requestId=ref('')
const routes=[['UK_air','英国空派'],['UK_truck','英国卡航'],['UK_sea','英国海运'],['DE_air','德国空派'],['DE_truck','德国卡航'],['DE_sea','德国海运']]
function id(){const s=Array.from(crypto.getRandomValues(new Uint8Array(16)),v=>v.toString(16).padStart(2,'0')).join('');return `${s.slice(0,8)}-${s.slice(8,12)}-${s.slice(12,16)}-${s.slice(16,20)}-${s.slice(20)}`}
let fingerprint=''
async function open(kind:string,items:any[]){if(!items.length)return;busy.value=true;error.value='';lock.value=false;rows.value=[];selection.value={kind,items:items.map(i=>({id:i.id,revision:i.revision}))};try{const d=await api('/procurement/flow/dispatch-preview',selection.value);rows.value=d.rows.map((r:any)=>({...r,quantities:{...Object.fromEntries(routes.map(([k])=>[k,0])),...r.quantities}}));requestId.value=id();fingerprint='';visible.value=true}catch(e){ElMessage.error((e as Error).message)}finally{busy.value=false}}
const sum=(row:any)=>Object.values(row.quantities).reduce((s:number,v:any)=>s+(Number(v)||0),0)
const invalid=computed(()=>!rows.value.length||rows.value.some(r=>!r.store||sum(r)<=0||sum(r)>r.quantity)||new Set(rows.value.map(r=>r.store)).size>1)
async function save(){if(busy.value||invalid.value)return;const body={...selection.value,lock_inventory:lock.value,rows:rows.value.map(r=>({key:r.key,store:r.store,quantities:Object.fromEntries(routes.map(([key])=>[key,r.quantities[key!]||0]))}))};const next=JSON.stringify(body);if(fingerprint&&next!==fingerprint)requestId.value=id();fingerprint=next;busy.value=true;error.value='';try{const d=await api('/procurement/flow/dispatch',{...body,request_id:requestId.value});visible.value=false;emit('completed');ElMessage.success(`已生成 ${d.created_ids.length} 个发货计划`)}catch(e){error.value=(e as Error).message}finally{busy.value=false}}
defineExpose({open})
</script>
<template><el-dialog v-model="visible" title="生成发货计划" width="min(1650px,96vw)" :close-on-click-modal="false" :show-close="!busy" :close-on-press-escape="!busy">
<el-alert v-if="error" :title="error" type="error" :closable="false"/>
<div class="dispatch-stock"><b>是否锁定库存？</b><el-radio-group v-model="lock" :disabled="busy"><el-radio :value="false">否，不锁定库存</el-radio><el-radio :value="true">是，校验并锁定库存</el-radio></el-radio-group></div>
<template v-if="true"><TableReset table-key="PurchaseDispatch/routes"/><el-table border :data="rows" max-height="480" data-width-key="PurchaseDispatch/routes" @header-dragend="(w:number,_old:number,c:any)=>widths.resize('routes',w,c)" @header-contextmenu="(c:any,e:MouseEvent)=>widths.resetColumn('routes',c,e)">
<el-table-column column-key="source" label="来源单号" :width="widths.width('routes','source',170)"><template #default="{row}">{{row.numbers.join(' / ')}}</template></el-table-column>
<el-table-column column-key="product" label="标品编码 / 品名" min-width="230" :width="widths.width('routes','product',undefined)"><template #default="{row}"><b>{{row.code}}</b><div>{{row.name}}</div></template></el-table-column>
<el-table-column column-key="warehouse" label="仓库" :width="widths.width('routes','warehouse',120)"><template #default="{row}">{{row.warehouse_name}}</template></el-table-column>
<el-table-column column-key="store" label="店铺（必填）" :width="widths.width('routes','store',145)"><template #default="{row}"><el-select v-model="row.store" :disabled="row.fixed_store||busy" placeholder="请选择店铺"><el-option v-for="s in props.stores" :key="s" :value="s" :label="s"/></el-select></template></el-table-column>
<el-table-column column-key="quantity" prop="quantity" label="可安排量" :width="widths.width('routes','quantity',95)" align="center"/>
<el-table-column v-for="[key,label] in routes" :key="key" :column-key="key" :label="label" :width="widths.width('routes',key!,115)" align="center"><template #default="{row}"><el-input-number v-model="row.quantities[key!]" :min="0" :max="row.quantity" :precision="0" :controls="false" :disabled="busy" style="width:100%" :aria-label="label+' '+row.code"/></template></el-table-column>
<el-table-column column-key="total" label="本次合计" :width="widths.width('routes','total',100)" align="center"><template #default="{row}"><span :class="{over:sum(row)>row.quantity}">{{sum(row)}}</span></template></el-table-column>
</el-table></template><p>请填写店铺及国家对应渠道的数量。每行合计不能超过可安排量；仅同仓库、同店铺可一起生成。已安排数量不会重复生成。</p>
<template #footer><el-button :disabled="busy" @click="visible=false">取消</el-button><el-button type="primary" :disabled="invalid" :loading="busy" @click="save">确认生成发货计划</el-button></template></el-dialog></template>
<style scoped>:deep(.el-table .cell){padding:0 8px;text-overflow:clip}.dispatch-stock{display:flex;gap:24px;align-items:center;margin:12px 0 20px}.over{color:#d44}p{color:#768b80;font-size:13px;line-height:1.8}</style>
