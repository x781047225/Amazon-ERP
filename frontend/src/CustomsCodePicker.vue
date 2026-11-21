<script setup lang="ts">
import {useTableWidths as useListTableWidths} from './tableWidths'
const listWidths=useListTableWidths('CustomsCodePickerLists')

import {ref,watch} from 'vue'
import {api} from './session'
import {ElMessage} from 'element-plus'
const props=defineProps<{modelValue:boolean,kind:'declaration'|'clearance'}>(),emit=defineEmits(['update:modelValue','select']),q=ref(''),page=ref(1),pageSize=ref(25),rows=ref<any[]>([]),total=ref(0),busy=ref(false)
async function load(){busy.value=true;try{const d=await api('/customs/codes/'+props.kind+'?'+new URLSearchParams({q:q.value,page:String(page.value),page_size:String(pageSize.value)}));rows.value=d.rows;total.value=d.total}catch(e){ElMessage.error((e as Error).message)}finally{busy.value=false}}
watch(()=>props.modelValue,v=>{if(v){q.value='';page.value=1;load()}})
function choose(row:any){emit('select',row);emit('update:modelValue',false)}
</script>
<template><el-dialog :model-value="modelValue" @update:model-value="emit('update:modelValue',$event)" :title="kind==='declaration'?'选择报关编码':'选择清关编码'" width="min(760px,94vw)" append-to-body><div class="picker-search"><el-input v-model="q" placeholder="输入中文商品名称 / HSCODE" clearable @keyup.enter="page=1;load()"/><el-button type="primary" @click="page=1;load()">搜索</el-button><TableReset table-key="CustomsCodePickerLists/list-1"/></div><template v-if="true"><el-table data-width-key="CustomsCodePickerLists/list-1" @header-dragend="(w:number,_:number,c:any)=>listWidths.resize('list-1',w,c)" @header-contextmenu="(c:any,e:MouseEvent)=>listWidths.resetColumn('list-1',c,e)" :data="rows" v-loading="busy" @row-dblclick="choose"><el-table-column :column-key="'name'" :width="listWidths.width('list-1','name',undefined)" prop="name" label="商品名称" min-width="200"/><el-table-column :column-key="'hs_code'" :width="listWidths.width('list-1','hs_code','170')" prop="hs_code" label="HSCODE" /><el-table-column :column-key="'column-3'" :width="listWidths.width('list-1','column-3','110')" label="税率"  align="center"><template #default="{row}">{{row.tax_rate}}%</template></el-table-column><el-table-column :column-key="'column-4'" :width="listWidths.width('list-1','column-4','80')" label="操作" ><template #default="{row}"><el-button type="primary" link @click="choose(row)">选择</el-button></template></el-table-column></el-table></template><AppPagination v-model:current-page="page" :total="total" @change="load" v-model:page-size="pageSize"/><p>没有找到时，可关闭窗口，在产品中手动填写 HSCODE 和税率。</p></el-dialog></template>
<style scoped>.picker-search{display:flex;gap:12px;margin-bottom:16px}.picker-search .el-input{max-width:360px}.el-pagination{margin-top:14px}p{color:#7d8d85;font-size:12px}</style>
