<script setup lang="ts">

import {ref,reactive,computed,watch,onMounted,onBeforeUnmount} from 'vue'
import {api} from './session'
import {useTableWidths} from './tableWidths'
const widths=useTableWidths('ShippingProductPicker')
const props=defineProps<{modelValue:any[],store:string,country:string,disabled:boolean,protectedIds?:number[]}>()
const emit=defineEmits(['update:modelValue','busy'])
const rows=ref<any[]>([]),total=ref(0),page=ref(1),pageSize=ref(25),q=ref(''),loading=ref(false),error=ref(''),pending=ref(0)
const picked=computed(()=>new Map(props.modelValue.map(i=>[i.product_id,i])))
const quantity=computed(()=>props.modelValue.reduce((n,i)=>n+(Number(i.quantity)||0),0))
let generation=0,context=0,alive=true
watch(pending,n=>emit('busy',n>0),{flush:'sync'})
async function load(){const n=++generation;loading.value=true;error.value='';try{const d=await api('/products?'+new URLSearchParams({q:q.value.trim(),page:String(page.value),page_size:String(pageSize.value)}));if(alive&&n===generation){rows.value=d.rows;total.value=d.total}}catch(e){if(alive&&n===generation)error.value=(e as Error).message}finally{if(n===generation)loading.value=false}}
function search(){page.value=1;load()}
async function candidates(item:any){const version=context;item.listing_id=null;item.candidates=[];pending.value++;try{const list=await api('/shipping/candidates?'+new URLSearchParams({store:props.store,country:props.country,product_id:String(item.product_id)}));if(alive&&version===context){item.candidates=list;item.listing_id=list.length===1?list[0].id:null}}catch(e){if(alive&&version===context)error.value=(e as Error).message}finally{pending.value--}}
async function toggle(product:any,checked:boolean){if(props.disabled)return;if(!checked){if(props.protectedIds?.includes(product.id))return;emit('update:modelValue',props.modelValue.filter(i=>i.product_id!==product.id));return}if(picked.value.has(product.id))return;const item=reactive({product_id:product.id,code:product.code,name:product.name,quantity:1,listing_id:null,candidates:[]});emit('update:modelValue',[...props.modelValue,item]);await candidates(item)}
watch(()=>[props.store,props.country],async()=>{context++;error.value='';await Promise.all(props.modelValue.map(candidates))})
onMounted(load)
onBeforeUnmount(()=>{alive=false;generation++;context++})
</script>
<template><section class="product-picker">
<div class="picker-toolbar"><el-input v-model="q" aria-label="搜索发货产品" placeholder="搜索标品编码 / 中文品名" clearable @keyup.enter="search"/><el-button @click="search">查询产品</el-button><span>已选 {{modelValue.length}} 款 · {{quantity}} 件</span><TableReset :table-key="'ShippingProductPicker/'+'products'"/></div>
<el-alert v-if="error" :title="error" type="error" :closable="false"/>
<template v-if="true"><el-table :data-width-key="'ShippingProductPicker/'+'products'"    :data="rows" row-key="id" v-loading="loading" max-height="430" border @header-dragend="(w:number,_old:number,c:any)=>widths.resize('products',w,c)">
<el-table-column :column-key="'column-1'" :width="widths.width('products','column-1','48')" ><template #default="{row}"><el-checkbox :model-value="picked.has(row.id)" :disabled="disabled||!!protectedIds?.includes(row.id)" :aria-label="'选择'+row.code" @change="(v:any)=>toggle(row,!!v)"/></template></el-table-column>
<el-table-column  :width="widths.width('products','product',260)" column-key="product" label="标品编码 / 品名" ><template #default="{row}"><b>{{row.code}}</b><div>{{row.name}}</div></template></el-table-column>
<el-table-column  :width="widths.width('products','listing',470)" column-key="listing" label="店铺 SKU / FNSKU" ><template #default="{row}"><el-select v-if="picked.has(row.id)" v-model="picked.get(row.id).listing_id" :aria-label="row.code+'的SKU'" :disabled="disabled||pending>0" :placeholder="pending?'正在读取 SKU':picked.get(row.id).candidates.length?'选择 SKU':'当前店铺/国家未关联 SKU，请先关联 Listing'" clearable><el-option v-for="c in picked.get(row.id).candidates" :key="c.id" :value="c.id" :label="c.sku+' · '+(c.fnsku||'FNSKU未返回')"/></el-select><span v-else class="muted">勾选产品后带出关联 SKU</span></template></el-table-column>
<el-table-column  :width="widths.width('products','quantity',190)" column-key="quantity" label="计划发货量"  align="center"><template #default="{row}"><el-input-number v-if="picked.has(row.id)" v-model="picked.get(row.id).quantity" :aria-label="row.code+'发货数量'" :min="1" :max="100000" :precision="0" :disabled="disabled"/><span v-else>—</span></template></el-table-column>
</el-table></template>
<AppPagination v-model:current-page="page" :total="total" @change="load()" v-model:page-size="pageSize"/>
<div v-if="modelValue.length" class="picked-products"><el-tag v-for="item in modelValue" :key="item.product_id" :closable="!disabled&&!protectedIds?.includes(item.product_id)" @close="toggle({id:item.product_id},false)">{{item.code}} · {{item.quantity||0}} 件</el-tag></div>
</section></template>
<style scoped>.product-picker{margin:10px 0 20px}.picker-toolbar{display:flex;align-items:center;gap:12px;margin-bottom:12px}.picker-toolbar .el-input{max-width:330px}.picker-toolbar span,.muted{color:#899b93;font-size:13px}.el-select{width:100%}.el-input-number{width:155px}.el-pagination{margin-top:10px}.picked-products{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}</style>
