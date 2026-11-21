<script setup lang="ts">
import {ref,reactive,watch} from 'vue'
import {ElMessage} from 'element-plus'
import {api} from './session'
const props=defineProps<{options:any,initial:any}>()
const emit=defineEmits(['close','saved'])
function uuid(){if(crypto.randomUUID)return crypto.randomUUID();const b=crypto.getRandomValues(new Uint8Array(16));b[6]=(b[6]&15)|64;b[8]=(b[8]&63)|128;const s=Array.from(b,v=>v.toString(16).padStart(2,'0')).join('');return `${s.slice(0,8)}-${s.slice(8,12)}-${s.slice(12,16)}-${s.slice(16,20)}-${s.slice(20)}`}
const form=reactive({store:props.initial.store||props.options.stores[0]||'',country:props.initial.country||'UK',channel:props.initial.channel||'sea',warehouse_id:props.initial.warehouse_id||null,ship_date:new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Shanghai'}).format(new Date()),lock_inventory:false})
const busy=ref(false),downloading=ref(false),error=ref(''),file=ref<{filename:string,content:string}|null>(null),preview=ref<any>(null),requestId=ref(uuid())
watch(form,()=>{preview.value=null;error.value='';requestId.value=uuid()})
async function downloadTemplate(){downloading.value=true;error.value='';try{const r=await fetch('/api/shipping/import-template');if(!r.ok){const d=await r.json();throw new Error(d.detail||'模板下载失败')}const url=URL.createObjectURL(await r.blob()),a=document.createElement('a');a.href=url;a.download='发货计划上传模板.xlsx';a.click();setTimeout(()=>URL.revokeObjectURL(url),30000)}catch(e){error.value=(e as Error).message}finally{downloading.value=false}}
async function pickFile(e:Event){
 file.value=null;preview.value=null;error.value='';requestId.value=uuid()
 const f=(e.target as HTMLInputElement).files?.[0];if(!f)return
 if(!f.name.toLowerCase().endsWith('.xlsx')||f.size>10000000){error.value='请选择10MB以内的xlsx文件';return}
 busy.value=true
 try{file.value={filename:f.name,content:await new Promise<string>((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).split(',')[1]);reader.onerror=()=>reject(new Error('文件读取失败，请重新选择'));reader.readAsDataURL(f)})}}
 catch(e){error.value=(e as Error).message}finally{busy.value=false}
 if(file.value)await validate()
}
function ready(){if(!form.store||!form.country||!form.channel||!form.warehouse_id||!form.ship_date){error.value='请选择店铺、国家、渠道、发货仓库和计划发货日期';return false}if(!file.value){error.value='请选择填写后的模板文件';return false}return true}
async function validate(){if(busy.value||!ready())return;busy.value=true;preview.value=null;error.value='';try{preview.value=(await api('/shipping/import-plan',{...form,...file.value,request_id:requestId.value})).preview}catch(e){error.value=(e as Error).message}finally{busy.value=false}}
async function save(){if(busy.value||!preview.value?.can_import||!ready())return;busy.value=true;error.value='';try{const result=await api('/shipping/import-plan',{...form,...file.value,request_id:requestId.value,confirm:true,preview_token:preview.value.preview_token});ElMessage.success('发货计划 '+result.number+' 已生成');emit('saved')}catch(e){error.value=(e as Error).message}finally{busy.value=false}}
</script>
<template>
<el-dialog :model-value="true" title="上传发货计划" width="min(1120px,96vw)" :close-on-click-modal="false" :close-on-press-escape="!busy" :show-close="!busy" @close="emit('close')">
 <p class="intro">一次上传生成一张发货计划。先选择发货信息，再下载模板填写 SKU、FNSKU、计划发货量，上传校验后确认生成。</p>
 <el-alert v-if="error" :title="error" type="error" :closable="false"/>
 <el-form label-position="top" :disabled="busy"><div class="upload-fields">
 <el-form-item label="店铺" required><el-select v-model="form.store" aria-label="上传店铺"><el-option v-for="s in options.stores" :key="s" :value="s" :label="s"/></el-select></el-form-item>
 <el-form-item label="国家" required><el-select v-model="form.country" aria-label="上传国家"><el-option v-for="(v,k) in options.countries" :key="k" :value="k" :label="String(v)"/></el-select></el-form-item>
 <el-form-item label="渠道" required><el-select v-model="form.channel" aria-label="上传渠道"><el-option v-for="(v,k) in options.channels" :key="k" :value="k" :label="String(v)"/></el-select></el-form-item>
 <el-form-item label="发货仓库" required><el-select v-model="form.warehouse_id" aria-label="上传发货仓库"><el-option v-for="w in options.warehouses" :key="w.id" :value="w.id" :label="w.name"/></el-select></el-form-item>
 <el-form-item label="计划发货日期" required><el-date-picker v-model="form.ship_date" type="date" value-format="YYYY-MM-DD" aria-label="上传计划发货日期"/></el-form-item>
 </div><el-form-item label="是否锁定库存"><el-radio-group v-model="form.lock_inventory"><el-radio :value="false">否，不锁定库存</el-radio><el-radio :value="true">是，校验并锁定库存</el-radio></el-radio-group></el-form-item></el-form>
 <div class="upload-actions"><el-button :loading="downloading" :disabled="busy" @click="downloadTemplate">下载模板</el-button><input type="file" accept=".xlsx" aria-label="选择发货计划文件" :disabled="busy" @change="pickFile"/><el-button :disabled="!file||busy" @click="validate">校验预览</el-button></div>
 <p class="intro">最多 200 行；同一 SKU 自动合并数量。FNSKU 留空时按本店铺、本站点的 SKU 带出；已填写的值会核对。SKU 须已关联有效标品。</p>
 <template v-if="preview"><el-alert v-if="!preview.can_import" title="请修正下方错误行后重新上传，当前文件不会生成计划。" type="error" :closable="false"/><p v-else class="preview-summary">校验通过 · {{preview.item_count}} 个 SKU · 计划发货 {{preview.quantity}} 件</p>
 <el-table :data="preview.rows" border max-height="380"><el-table-column prop="row" label="Excel行" width="85"/><el-table-column prop="sku" label="SKU" min-width="190"/><el-table-column prop="fnsku" label="FNSKU" width="145"/><el-table-column prop="name" label="标品品名" min-width="140"/><el-table-column prop="quantity" label="计划发货量" width="120" align="center"/><el-table-column label="校验结果" min-width="210"><template #default="{row}"><span :class="row.error?'row-error':'row-valid'">{{row.error||'通过'}}</span></template></el-table-column></el-table>
 </template>
 <template #footer><el-button :disabled="busy" @click="emit('close')">取消</el-button><el-button type="primary" :loading="busy" :disabled="!preview?.can_import" @click="save">确认生成发货计划</el-button></template>
</el-dialog>
</template>
<style scoped>
.intro{color:#71877c;line-height:1.7}.upload-fields{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:0 18px;margin-top:18px}.upload-fields :deep(.el-select),.upload-fields :deep(.el-date-editor){width:100%}.upload-actions{display:flex;align-items:center;gap:16px;flex-wrap:wrap}.preview-summary,.row-valid{color:#20745f}.row-error{color:#c03639}@media(max-width:700px){.upload-fields{grid-template-columns:1fr 1fr}}
</style>
