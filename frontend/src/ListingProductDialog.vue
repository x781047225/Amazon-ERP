<script setup lang="ts">
import {useTableWidths} from './tableWidths'
const tableWidths=useTableWidths('ListingProductDialog')

import {ref,watch} from 'vue'
import {ElMessage,ElMessageBox} from 'element-plus'
import {api} from './session'
import type {Listing} from './listingTypes'
const props=defineProps<{row:Listing|null}>(),emit=defineEmits(['close','saved'])
const q=ref(''),page=ref(1),total=ref(0),rows=ref<any[]>([]),current=ref<any>(null),loading=ref(false),busy=ref(false),ready=ref(false),error=ref('')
let sequence=0,epoch=0
async function search(reset=true){if(reset)page.value=1;const seq=++sequence;loading.value=true;try{const d=await api('/products?'+new URLSearchParams({q:q.value,page:String(page.value),page_size:'8'}));if(seq===sequence){rows.value=d.rows;total.value=d.total}}catch(e){error.value=(e as Error).message}finally{if(seq===sequence)loading.value=false}}
watch(()=>props.row,async r=>{epoch++;sequence++;ready.value=false;rows.value=[];error.value='';if(!r)return;const id=epoch;q.value=r.sku;page.value=1;current.value=null;try{const d=await api('/products/listing-link/'+r.id);if(id!==epoch)return;current.value=d.product;ready.value=true;await search()}catch(e){if(id===epoch)error.value=(e as Error).message}},{immediate:true})
function close(){if(!busy.value)emit('close')}
async function bind(p:any|null){
 if(!props.row||!ready.value||busy.value)return
 if(p?.id===current.value?.id)return
 if(current.value){try{await ElMessageBox.confirm(p?`将 ${props.row.sku} 从 ${current.value.code} 改为 ${p.code}？`:`解除 ${props.row.sku} 与 ${current.value.code} 的关联？`,p?'更换关联':'解除关联',{confirmButtonText:'确认',cancelButtonText:'取消',type:'warning'})}catch{return}}
 busy.value=true
 try{await api('/products/links/apply',{product_id:p?.id??null,items:[{listing_id:props.row.id,expected_product_id:current.value?.id??null}]});current.value=p;ElMessage.success(p?'已关联到 '+p.code:'已解除关联');emit('saved');emit('close')}catch(e){error.value=(e as Error).message;if(props.row){const d=await api('/products/listing-link/'+props.row.id).catch(()=>null);if(d)current.value=d.product}}finally{busy.value=false}
}
</script>
<template>
<el-dialog :model-value="!!row" title="关联本地商品" width="920px" :close-on-click-modal="false" :close-on-press-escape="!busy" :show-close="!busy" @close="close">
 <template v-if="row"><div class="link-context"><b>{{row.sku}}</b><span>{{row.store}} · {{row.marketplace}} · {{row.asin}}</span><p>当前标品：{{current?current.code+' · '+current.name:'未关联'}} <el-button v-if="current" link type="danger" :disabled="!ready||busy" @click="bind(null)">解除关联</el-button></p></div>
 <div class="link-search"><el-input v-model="q" placeholder="搜索 GM 编码 / 原编码 / 品名" clearable @keyup.enter="search()"/><el-button type="primary" :loading="loading" @click="search()">查询</el-button><el-button @click="q='';search()">全部商品</el-button><TableReset table-key="ListingProductDialog/table-1"/></div>
 <el-alert v-if="error" :title="error" type="error" :closable="false"/><p class="hint">核对颜色、规格和包装数量后，点击对应商品的“关联”。</p>
 <template v-if="true"><el-table border data-width-key="ListingProductDialog/table-1" @header-dragend="(width: number, _old: number, column: any) => tableWidths.resize('table-1', width, column)" @header-contextmenu="(column: any, event: MouseEvent) => tableWidths.resetColumn('table-1', column, event)" :data="rows" v-loading="loading" max-height="410" empty-text="没有匹配商品，请尝试产品名称或点击全部商品">
 <el-table-column :column-key="'column-1'" :width="tableWidths.width('table-1', 'column-1', undefined)" label="本地商品" min-width="290"><template #default="{row:p}"><b>{{p.code}}</b><div>{{p.name}}</div><small v-if="p.aliases?.length">原编码：{{p.aliases.join(' / ')}}</small></template></el-table-column>
 <el-table-column :column-key="'column-2'" :width="tableWidths.width('table-1', 'column-2', undefined)" label="数量 / 规格" min-width="180"><template #default="{row:p}">{{p.pack_quantity? p.pack_quantity+'P':'数量待核对'}}<div>{{p.specification}}</div><small v-for="note in p.review_notes.filter((n:string)=>n!=='数量待核对')" :key="note" class="review">{{note}}</small></template></el-table-column>
 <el-table-column :column-key="'column-3'" label="采购成本 CNY" :width="tableWidths.width('table-1', 'column-3', '130')"><template #default="{row:p}">{{p.cost_cny==null?'未维护':Number(p.cost_cny).toFixed(2)}}</template></el-table-column>
 <el-table-column :column-key="'column-4'" label="操作" :width="tableWidths.width('table-1', 'column-4', '95')"><template #default="{row:p}"><el-button link type="primary" :disabled="!ready||busy||current?.id===p.id" @click="bind(p)">{{current?.id===p.id?'已关联':'关联'}}</el-button></template></el-table-column>
 </el-table></template>
 <el-pagination v-model:current-page="page" :total="total" :page-size="8" layout="total,prev,pager,next" @current-change="search(false)"/>
 </template><template #footer><el-button :disabled="busy" @click="close">关闭</el-button></template>
</el-dialog>
</template>
<style scoped>
.link-context{padding:14px 18px;background:#f2f7f4;border-radius:8px}.link-context span{display:block;color:#83968a;margin-top:5px;font-size:12px}.link-context p{margin:8px 0 0;font-size:13px}.link-search{display:flex;gap:10px;margin:20px 0 12px}.hint,small{font-size:12px;color:#83968a}small{display:block;overflow-wrap:anywhere}.review{color:#ac7d35}.el-pagination{justify-content:flex-end;margin-top:16px}
</style>
