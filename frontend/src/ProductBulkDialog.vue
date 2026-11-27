<script setup lang="ts">
import {ref,reactive,computed} from 'vue'
import {ElMessage} from 'element-plus'
import {api} from './session'
const props=defineProps<{rows:any[],kind:string,suppliers:string[],suppliersLoading?:boolean}>(),emit=defineEmits(['close','saved','refresh-suppliers'])
const labels:Record<string,string>={supplier:'供应商',packaging:'产品包装',weight:'产品重量',cost:'成本价'}
const dimensions:Record<string,string>={package_length_cm:'长',package_width_cm:'宽',package_height_cm:'高'}
const form=reactive<any>({supplier:'',package_length_cm:null,package_width_cm:null,package_height_cm:null,gross_weight_g:null,cost_cny:null,recalculate_freight:true})
const busy=ref(false),error=ref('')
const keys=computed(()=>({supplier:['supplier'],packaging:Object.keys(dimensions),weight:['gross_weight_g','recalculate_freight'],cost:['cost_cny']} as Record<string,string[]>)[props.kind])
async function save(){if(busy.value)return;error.value='';if(keys.value.some(k=>k!=='recalculate_freight'&&(form[k]===null||form[k]===''||form[k]===undefined))){error.value='请填写完整修改内容';return}busy.value=true;try{const values=Object.fromEntries(keys.value.map(k=>[k,typeof form[k]==='number'?String(form[k]):form[k]]));await api('/products/bulk',{kind:props.kind,items:props.rows.map(r=>({id:r.id,revision:r.revision})),...values});ElMessage.success(`已保存 ${props.rows.length} 个产品`);emit('saved')}catch(e){error.value=(e as Error).message}finally{busy.value=false}}
function close(){if(!busy.value)emit('close')}
</script>
<template><el-dialog :model-value="true" :title="'批量修改 · '+labels[kind]" width="620px" :before-close="close" :close-on-click-modal="false" :show-close="!busy" :close-on-press-escape="!busy"><p>已选择 {{rows.length}} 个产品</p><el-alert v-if="error" :title="error" type="error" :closable="false"/><el-form label-position="top" :disabled="busy">
<el-form-item v-if="kind==='supplier'" label="供应商" required><el-select v-model="form.supplier" :loading="suppliersLoading" @visible-change="(open:boolean)=>open&&emit('refresh-suppliers')" filterable placeholder="选择供应商" aria-label="批量供应商" style="width:100%"><el-option v-for="s in suppliers" :key="s" :value="s" :label="s"/></el-select><small>设为主供应商，保留已关联的其他供应商。</small></el-form-item>
<div v-if="kind==='packaging'" class="dimensions"><el-form-item v-for="(label,key) in dimensions" :key="key" :label="label+'（cm）'" required><el-input-number v-model="form[key]" :min="0.0001" :max="9999999999" :precision="4" :controls="false" :aria-label="'包装'+label"/></el-form-item></div>
<template v-if="kind==='weight'"><el-form-item label="产品重量（g）" required><el-input-number v-model="form.gross_weight_g" :min="0.0001" :max="9999999999" :precision="4" :controls="false" aria-label="批量产品重量"/></el-form-item><el-checkbox v-model="form.recalculate_freight">按新重量重新计算头程</el-checkbox><p class="muted">英国 ¥15/kg，欧洲 ¥20/kg；勾选后替换手动维护的头程金额。</p></template>
<el-form-item v-if="kind==='cost'" label="成本价（CNY）" required><el-input-number v-model="form.cost_cny" :min="0" :max="9999999999999" :precision="4" :controls="false" aria-label="批量成本价"/></el-form-item>
</el-form><el-table :data="rows" max-height="230" border><el-table-column prop="code" label="标品编码"/><el-table-column prop="name" label="品名"/></el-table><template #footer><el-button :disabled="busy" @click="close">取消</el-button><el-button type="primary" :loading="busy" @click="save">保存修改</el-button></template></el-dialog></template>
<style scoped>.dimensions{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px}.dimensions :deep(.el-input-number){width:100%}.muted,small{font-size:12px;color:#829187}small{display:block;margin-top:8px}</style>
