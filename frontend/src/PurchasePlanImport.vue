<script setup lang="ts">
import {useTableWidths} from './tableWidths'
const tableWidths=useTableWidths('PurchasePlanImport')

import {ref} from 'vue'
import {ElMessage} from 'element-plus'
import {api} from './session'
const emit=defineEmits<{imported:[]}>()
const visible=ref(false),busy=ref(false),error=ref(''),preview=ref<any>(null),upload=ref<any>(null),requestId=ref('')
const routes=['英国空派','英国卡航','英国海运','德国空派','德国卡航','德国海运']
function open(){visible.value=true;error.value='';preview.value=null;upload.value=null}
function uuid(){const a=crypto.getRandomValues(new Uint8Array(16));a[6]=(a[6]!&15)|64;a[8]=(a[8]!&63)|128;const s=Array.from(a,v=>v.toString(16).padStart(2,'0')).join('');return `${s.slice(0,8)}-${s.slice(8,12)}-${s.slice(12,16)}-${s.slice(16,20)}-${s.slice(20)}`}
async function selectFile(event:Event){
 const input=event.target as HTMLInputElement,file=input.files?.[0];if(!file)return
 preview.value=null;upload.value=null;error.value=''
 if(!file.name.toLowerCase().endsWith('.xlsx')||file.size>5*1024*1024){error.value='请选择 5MB 以内的 .xlsx 文件';input.value='';return}
 busy.value=true
 try{
  const content=await new Promise<string>((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).split(',')[1]!);reader.onerror=()=>reject(new Error('读取文件失败'));reader.readAsDataURL(file)})
  upload.value={filename:file.name,content};preview.value=await api('/procurement/plan-import/preview',upload.value);requestId.value=uuid()
 }catch(e){error.value=(e as Error).message}finally{busy.value=false;input.value=''}
}
async function commit(){if(busy.value||!preview.value)return;busy.value=true;error.value='';try{const d=await api('/procurement/plan-import/commit',{...upload.value,request_id:requestId.value,preview_hash:preview.value.preview_hash});visible.value=false;ElMessage.success(`已生成 1 个采购批次，共 ${d.product_count} 个标品、${d.quantity} 件`);emit('imported')}catch(e){error.value=(e as Error).message}finally{busy.value=false}}
</script>
<template>
 <el-button @click="open">Excel 导入</el-button>
 <el-dialog v-model="visible" title="Excel 导入采购计划" width="min(1240px, 96vw)" :close-on-click-modal="false" :close-on-press-escape="!busy" :show-close="!busy">
  <p>整张表生成一个采购批次，展开后按标品查看六个渠道数量。空白或 0 不生成计划；重复的商品、仓库、店铺会汇总。</p>
  <p>SKU 可填商品中心标品编码，或该店铺已关联的亚马逊 SKU。仓库、店铺请填写系统中的名称。最多 1000 行，数量单位为标品销售包装。</p>
  <div class="import-actions"><a href="/api/procurement/plan-import/template" download>下载 Excel 模板</a><label class="file-picker">{{busy?'正在处理…':'选择填写好的 Excel'}}<input type="file" accept=".xlsx" :disabled="busy" @change="selectFile"/></label><span>{{upload?.filename}}</span></div>
  <el-alert v-if="error" type="error" :title="error" :closable="false" class="import-error"/>
  <div v-if="preview" v-loading="busy">
   <p class="import-total">将生成 1 个批次 · {{preview.product_count}} 个标品 · {{preview.quantity}} 件<span v-if="preview.skipped_rows"> · 已忽略 {{preview.skipped_rows}} 行全零数量</span></p>
   <template v-if="true"><TableReset table-key="PurchasePlanImport/table-1"/><el-table data-width-key="PurchasePlanImport/table-1" @header-dragend="(width: number, _old: number, column: any) => tableWidths.resize('table-1', width, column)" @header-contextmenu="(column: any, event: MouseEvent) => tableWidths.resetColumn('table-1', column, event)" :data="preview.rows" max-height="480" border>
    <el-table-column :column-key="'column-1'" :width="tableWidths.width('table-1', 'column-1', undefined)" label="标品编码 / 品名" min-width="260"><template #default="{row}"><strong>{{row.code}}</strong><div>{{row.name}}</div></template></el-table-column>
    <el-table-column :column-key="'store'" prop="store" label="店铺" :width="tableWidths.width('table-1', 'store', '110')"/><el-table-column :column-key="'warehouse_name'" prop="warehouse_name" label="仓库" :width="tableWidths.width('table-1', 'warehouse_name', '120')"/>
    <el-table-column :column-key="'column-4:' + (name)" v-for="(name,i) in routes" :key="name" :label="name" :width="tableWidths.width('table-1', 'column-4:' + (name), '100')" align="center"><template #default="{row}">{{row.quantities[i]||'—'}}</template></el-table-column>
   </el-table></template>
  </div>
  <template #footer><el-button :disabled="busy" @click="visible=false">取消</el-button><el-button type="primary" :disabled="!preview" :loading="busy" @click="commit">确认生成一个采购批次</el-button></template>
 </el-dialog>
</template>
<style scoped>
p{line-height:1.8;color:#61796d}.import-actions{display:flex;gap:20px;align-items:center;margin:20px 0;flex-wrap:wrap}.import-actions a{color:#207860}.file-picker{display:inline-block;border:1px solid #d6e4dc;padding:8px 16px;border-radius:6px;cursor:pointer;color:#207860}.file-picker input{display:none}.import-error{white-space:pre-line;margin:16px 0}.import-total{font-weight:600;color:#23735d}
</style>
