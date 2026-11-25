<script setup lang="ts">
import CopyValue from './CopyValue.vue'
import {ref,computed,onMounted} from 'vue'
import {api,can} from './session'
import {ElMessage,ElMessageBox} from 'element-plus'
const rows=ref<any[]>([]),q=ref(''),page=ref(1),pageSize=ref(25),total=ref(0),busy=ref(false),error=ref('')
const editable=computed(()=>can('products.declaration','operate')||can('products.clearance','operate'))
const emit=defineEmits<{open:[id?:number]}>()
async function load(){busy.value=true;try{const d=await api('/customs/profiles?'+new URLSearchParams({q:q.value,page:String(page.value),page_size:String(pageSize.value)}));rows.value=d.rows;total.value=d.total;error.value=''}catch(e){error.value=(e as Error).message}finally{busy.value=false}}
function edit(row?:any){emit('open',row?.id)}
defineExpose({refresh:load})
async function remove(row:any){try{await ElMessageBox.confirm('删除「'+row.name+'」？已关联产品保留原有资料，只解除关联。','删除资料',{type:'warning',confirmButtonText:'删除',cancelButtonText:'取消'})}catch{return}try{await api('/customs/profiles/'+row.id+'/delete',{revision:row.revision});await load()}catch(e){ElMessage.error((e as Error).message)}}
onMounted(load)
</script>
<template><section class="customs-library">
 <div class="tools"><el-input v-model="q" aria-label="查询资料商品名称" placeholder="按商品名称查询" clearable @keyup.enter="page=1;load()"/><el-button type="primary" @click="page=1;load()">查询</el-button><el-button @click="q='';page=1;load()">重置</el-button><el-button v-if="editable" type="primary" @click="edit()">新增资料</el-button><el-button @click="load()">刷新</el-button></div>
 <p>一份资料可供多个商品选择。选择后带入产品并保存；修改资料库不会覆盖已保存的产品，未关联的旧产品保持原样。</p>
 <el-alert v-if="error" :title="error" type="error" :closable="false"/>
 <el-table :data="rows" border v-loading="busy" row-key="id">
  <el-table-column label="商品名称" prop="name" min-width="220"/>
  <el-table-column label="中文报关名" min-width="170"><template #default="{row}">{{row.declaration.name_cn||'—'}}</template></el-table-column>
  <el-table-column label="报关 HSCODE" min-width="150"><template #default="{row}">{{row.declaration.hs_code||'—'}}<CopyValue label="HS编码" :value="row.declaration.hs_code"/></template></el-table-column>
  <el-table-column label="中文清关名" min-width="170"><template #default="{row}">{{row.clearance.name_cn||'—'}}</template></el-table-column>
  <el-table-column label="清关 HS 编码" min-width="150"><template #default="{row}">{{row.clearance.hs_code||'—'}}<CopyValue label="HS编码" :value="row.clearance.hs_code"/></template></el-table-column>
  <el-table-column label="清关税率" width="110" align="center"><template #default="{row}">{{row.clearance.tax_rate==null||row.clearance.tax_rate===''?'—':Number(row.clearance.tax_rate)+'%'}}</template></el-table-column>
  <el-table-column label="中文材质" min-width="150"><template #default="{row}">{{row.clearance.material_cn||row.declaration.material_cn||'—'}}</template></el-table-column>
  <el-table-column label="中文用途" min-width="150"><template #default="{row}">{{row.clearance.usage_cn||row.declaration.usage_cn||'—'}}</template></el-table-column>
  <el-table-column label="关联商品" prop="linked_count" width="100"/>
  <el-table-column label="操作" width="150"><template #default="{row}"><el-button link type="primary" @click="edit(row)">{{editable?'编辑':'查看'}}</el-button><el-button v-if="editable" link type="danger" @click="remove(row)">删除</el-button></template></el-table-column>
 </el-table>
 <AppPagination v-model:current-page="page" v-model:page-size="pageSize" :total="total" @change="load()"/>
</section></template>
<style scoped>.tools{display:flex;gap:12px;margin:18px 0;flex-wrap:wrap}.tools .el-input{width:300px}.customs-library p{color:#7a8c84;font-size:13px}.customs-library h3{border-top:1px solid #e4eae7;padding-top:20px}</style>
