<script setup lang="ts">
import {ref,onMounted} from 'vue'
import {ElMessage,ElMessageBox} from 'element-plus'
import {api} from './session'
const props=defineProps<{rows:any[]}>(),emit=defineEmits(['close','saved'])
const q=ref(''),page=ref(1),total=ref(0),products=ref<any[]>([]),loading=ref(false),busy=ref(false),error=ref('');let sequence=0
async function search(){const seq=++sequence;loading.value=true;try{const d=await api('/products?'+new URLSearchParams({q:q.value,page:String(page.value),page_size:'20'}));if(seq===sequence){products.value=d.rows;total.value=d.total;error.value=''}}catch(e){if(seq===sequence)error.value=(e as Error).message}finally{if(seq===sequence)loading.value=false}}
async function bind(product:any){
 if(busy.value)return
 try{await ElMessageBox.confirm(`将勾选的 ${props.rows.length} 个 Listing 关联到 ${product.code} · ${product.name}？已有的标品关联将替换。`,'批量关联标品',{confirmButtonText:'确认关联',cancelButtonText:'取消'})}catch{return}
 busy.value=true;error.value=''
 try{await api('/products/links/apply',{product_id:product.id,items:props.rows.map(r=>({listing_id:r.id,expected_product_id:r.local_product?.id??null}))});ElMessage.success(`已将 ${props.rows.length} 个 Listing 关联到 ${product.code}`);emit('saved');emit('close')}catch(e){error.value=(e as Error).message}finally{busy.value=false}
}
onMounted(search)
</script>
<template><el-dialog :model-value="true" title="批量关联标品" width="920px" :close-on-click-modal="false" :close-on-press-escape="!busy" :show-close="!busy" @close="!busy&&emit('close')"><p>已选择 {{rows.length}} 个 Listing，统一关联到下方选定的一个标品。</p><p class="muted">{{rows.map(r=>r.sku+' · '+r.store+' / '+r.marketplace).join('；')}}</p><el-alert v-if="error" :title="error" type="error" :closable="false"/><div class="bulk-link-search"><el-input v-model="q" placeholder="搜索标品编码 / 品名" aria-label="搜索关联标品" :disabled="busy" @keyup.enter="page=1;search()"/><el-button :disabled="busy" @click="page=1;search()">查询</el-button></div><el-table :data="products" border v-loading="loading"><el-table-column prop="code" label="标品编码"/><el-table-column prop="name" label="品名"/><el-table-column label="操作" width="150"><template #default="{row}"><el-button link type="primary" :disabled="busy||loading" @click="bind(row)">关联到此标品</el-button></template></el-table-column></el-table><el-pagination v-model:current-page="page" :total="total" :page-size="20" layout="prev,pager,next,total" :disabled="busy" @current-change="search"/><template #footer><el-button :disabled="busy" @click="emit('close')">关闭</el-button></template></el-dialog></template>
<style scoped>.bulk-link-search{display:flex;gap:12px;margin:16px 0}.bulk-link-search .el-input{width:340px}.muted{font-size:12px;color:#7a8c84;max-height:100px;overflow:auto}</style>
