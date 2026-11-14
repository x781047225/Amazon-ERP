<script setup lang="ts">
import {useTableWidths} from './tableWidths'
const widths=useTableWidths('ShipmentDocuments')
import {ref,reactive,watch,onMounted} from 'vue'
import {api} from './session'
import {ElMessage} from 'element-plus'
const props=defineProps<{kind:string}>(),rows=ref<any[]>([]),q=ref(''),page=ref(1),total=ref(0),error=ref('')
async function load(){try{const d=await api('/logistics/documents?'+new URLSearchParams({kind:props.kind,q:q.value,page:String(page.value)}));rows.value=d.rows;total.value=d.total}catch(e){error.value=(e as Error).message}}
watch(()=>props.kind,()=>{page.value=1;load()});onMounted(load);defineExpose({load})
</script>
<template><section class="panel" style="padding:16px"><div style="display:flex;gap:12px;margin-bottom:16px"><el-input v-model="q" placeholder="搜索 FBA 号" style="width:260px" @keyup.enter="page=1;load()"/><el-button @click="page=1;load()">查询</el-button><TableReset table-key="ShipmentDocuments/table-1"/></div><el-alert v-if="error" :title="error" type="error"/><template v-if="true"><el-table :data="rows" border data-width-key="ShipmentDocuments/table-1" @header-dragend="(w:number,_:number,c:any)=>widths.resize('table-1',w,c)"><el-table-column column-key="c1" :width="widths.width('table-1','c1')" prop="shipment_id" label="FBA号"/><el-table-column column-key="c2" :width="widths.width('table-1','c2')" prop="store" label="店铺"/><el-table-column column-key="c3" :width="widths.width('table-1','c3')" prop="filename" label="文件名"/><el-table-column column-key="c4" :width="widths.width('table-1','c4')" prop="actor" label="生成人"/><el-table-column column-key="c5" :width="widths.width('table-1','c5')" label="生成时间"><template #default="{row}">{{new Date(row.created_at).toLocaleString('zh-CN')}}</template></el-table-column><el-table-column column-key="c6" :width="widths.width('table-1','c6')" label="操作"><template #default="{row}"><a :href="`/api/logistics/documents/${row.id}/download`">下载</a></template></el-table-column></el-table></template><el-pagination v-model:current-page="page" :total="total" :page-size="30" @current-change="load"/></section></template>
