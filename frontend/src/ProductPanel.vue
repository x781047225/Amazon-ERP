<script setup lang="ts">
import CopyValue from './CopyValue.vue'
import {can,firstAllowed} from './session'
import {useTableWidths} from './tableWidths'
const tableWidths=useTableWidths('ProductPanel')

import ProductBulkDialog from './ProductBulkDialog.vue'
import ProductLog from './ProductLog.vue'
import SupplierPanel from './SupplierPanel.vue'
import CustomsProfilePanel from './CustomsProfilePanel.vue'
import CustomsCodesPanel from './CustomsCodesPanel.vue'
import {ref,reactive,onMounted,computed} from 'vue'
import {ElMessage,ElMessageBox} from 'element-plus'
import {api,session} from './session'
const props=defineProps<{initialStore:string}>()
const tab=ref(can('products.products')?'products':can('products.suppliers')?'suppliers':can('settings.clearance')?'clearance-codes':can('settings.declaration')?'declaration-codes':firstAllowed('products',['declaration','clearance']))
const rows=ref<any[]>([]),total=ref(0),page=ref(1),pageSize=ref(25),loading=ref(false),error=ref('')
const summary=reactive({active_count:0,unlinked_listings:0}),options=reactive({suppliers:[] as string[],stores:[] as string[],markets:[] as string[]})
const filter=reactive({q:'',supplier:'',archived:false,unlinked:false,no_cost:false,no_freight:false,no_supplier:false})
const focusedProduct=ref(''),checked=ref<any[]>([]),bulkRows=ref<any[]>([]),bulkKind=ref(''),logRow=ref<any>(null),productTable=ref<any>()
async function openBulk(kind:string){bulkRows.value=checked.value.map(r=>({...r}));if(kind==='supplier')await refreshSuppliers();bulkKind.value=kind}
async function bulkSaved(){bulkKind.value='';await Promise.all([load(),refreshOptions()])}
const names:Record<string,string>={DE:'德国',UK:'英国',FR:'法国',IT:'意大利',ES:'西班牙',NL:'荷兰',BE:'比利时',PL:'波兰',SE:'瑞典',IE:'爱尔兰',AE:'阿联酋',SA:'沙特'}
const admin=computed(()=>can('products.'+tab.value,'operate'))
let sequence=0
async function load(){
 if(!can('products.products'))return;
 const seq=++sequence;loading.value=true;error.value='';checked.value=[];productTable.value?.clearSelection()
 const f={...filter,archived:String(filter.archived),unlinked:String(filter.unlinked),no_cost:String(filter.no_cost),no_freight:String(filter.no_freight),no_supplier:String(filter.no_supplier)}
 try{const data=await api('/products?'+new URLSearchParams(Object.fromEntries(Object.entries({...f,page:String(page.value),page_size:String(pageSize.value)}).filter(([key,value])=>key!=='product_id'||value!==''))));if(seq!==sequence)return;rows.value=data.rows;total.value=data.total;Object.assign(summary,{active_count:data.active_count,unlinked_listings:data.unlinked_listings})}catch(e){if(seq===sequence)error.value=(e as Error).message}finally{if(seq===sequence)loading.value=false}
}
function search(){page.value=1;load()}
function reset(){Object.assign(filter,{q:'',supplier:'',archived:false,unlinked:false,no_cost:false,no_freight:false,no_supplier:false});search()}
const suppliersLoading=ref(false)
async function refreshSuppliers(open=true){if(!open||!can('products.products')||suppliersLoading.value)return;suppliersLoading.value=true;try{options.suppliers=(await api('/products/options')).suppliers}catch(e){ElMessage.error('供应商列表更新失败：'+(e as Error).message)}finally{suppliersLoading.value=false}}
async function refreshOptions(){if(!can('products.products'))return;const [o,p]=await Promise.all([api('/options'),api('/products/options')]);Object.assign(options,{stores:o.stores,markets:o.markets,suppliers:p.suppliers})}
const linkedDialog=ref(false),linkedRows=ref<any[]>([]),linkedTotal=ref(0),linkedPage=ref(1),linkedPageSize=ref(25),linkedId=ref(0),linkedBusy=ref(false)
async function showLinks(row:any){linkedId.value=row.id;linkedPage.value=1;focusedProduct.value=row.code;linkedDialog.value=true;await loadLinks()}
async function loadLinks(){linkedBusy.value=true;try{const d=await api('/products/listings?'+new URLSearchParams({product_id:String(linkedId.value),state:'all',page:String(linkedPage.value),page_size:String(linkedPageSize.value)}));linkedRows.value=d.rows;linkedTotal.value=d.total}catch(e){ElMessage.error((e as Error).message)}finally{linkedBusy.value=false}}
const emit=defineEmits(['open-product','copy-product','open-customs'])
const customsLibrary=ref<{refresh:()=>void}|null>(null)
function edit(row?:any){emit('open-product',row?.id)}
defineExpose({canLeave:async()=>{if(bulkKind.value){ElMessage.info('请先关闭批量修改窗口');return false}return true},refresh:()=>{load();refreshOptions();customsLibrary.value?.refresh()}})
async function archiveProduct(row:any,restore=false){
 try{await ElMessageBox.confirm(restore?'恢复后即可编辑和关联店铺 SKU。':'将「'+row.code+'」移入回收站？以后可以恢复；已关联 SKU 的商品不能删除。',restore?'恢复标品':'删除标品',{type:'warning',confirmButtonText:restore?'恢复':'移入回收站',cancelButtonText:'取消'})}catch{return}
 try{await api('/products/'+row.id+(restore?'/restore':'/archive'),{revision:row.revision});ElMessage.success(restore?'标品已恢复':'已移入回收站');await Promise.all([load(),refreshOptions()])}catch(e){ElMessage.error((e as Error).message)}
}
const cost=(n:any)=>n==null?'未维护':'¥'+Number(n).toLocaleString('zh-CN',{minimumFractionDigits:2,maximumFractionDigits:4})
const time=(s:string)=>s?new Date(s).toLocaleString('zh-CN',{hour12:false}):'—'
const actionNames:Record<string,string>={product_packaging_import:'导入包装规格与重量',product_merge:'合并统一编码商品',product_normalize:'统一 GM 编码',product_import:'从领星导入',product_create:'新增',product_update:'修改',product_archive:'移入回收站',product_restore:'恢复'}

onMounted(async()=>{try{await refreshOptions()}catch(e){error.value=(e as Error).message};await load()})
</script>
<template>
<div class="product-center">
 
 <div class="catalog-nav"><el-radio-group v-model="tab"><el-radio-button v-if="can('products.products')" value="products">产品信息</el-radio-button><el-radio-button v-if="can('products.suppliers')" value="suppliers">供应商</el-radio-button><el-radio-button v-if="can('settings.clearance')" value="clearance-codes">清关编码</el-radio-button><el-radio-button v-if="can('settings.declaration')" value="declaration-codes">报关编码</el-radio-button><el-radio-button v-if="can('products.declaration')||can('products.clearance')" :value="can('products.declaration')?'declaration':'clearance'">清关&amp;报关资料</el-radio-button></el-radio-group></div><SupplierPanel v-if="tab==='suppliers'" @changed="refreshSuppliers()"/><CustomsCodesPanel v-else-if="tab==='clearance-codes'||tab==='declaration-codes'" :kind="tab==='clearance-codes'?'clearance':'declaration'"/><CustomsProfilePanel v-else-if="tab==='declaration'||tab==='clearance'" ref="customsLibrary" @open="id=>emit('open-customs',id)"/><template v-else>
 <div class="catalog-stats"><span>在用标品 <b>{{summary.active_count.toLocaleString()}}</b></span><span>可访问店铺待关联 SKU <b>{{summary.unlinked_listings.toLocaleString()}}</b></span></div>
 <section class="catalog-card">
  <div class="catalog-filters"><el-input v-model="filter.q" placeholder="搜索 GM 编码 / 原编码 / 品名" clearable @keyup.enter="search"/><el-select v-model="filter.supplier" :value-on-clear="''" placeholder="全部供应商" clearable filterable :loading="suppliersLoading" @visible-change="refreshSuppliers"><el-option v-for="s in options.suppliers" :key="s" :label="s" :value="s"/></el-select><el-checkbox v-model="filter.unlinked" @change="search">未关联</el-checkbox><el-checkbox v-model="filter.no_cost" @change="search">无成本</el-checkbox><el-tooltip content="英国或欧洲任一头程为空或0"><el-checkbox v-model="filter.no_freight" @change="search">无头程</el-checkbox></el-tooltip><el-checkbox v-model="filter.no_supplier" @change="search">无供应商</el-checkbox><el-switch v-model="filter.archived" active-text="回收站" @change="search"/><el-button type="primary" @click="search">查询</el-button><el-button @click="reset">重置</el-button><el-button v-if="admin" type="primary" @click="edit()">新增产品</el-button><el-dropdown v-if="admin&&!filter.archived" trigger="click" :disabled="!checked.length||loading" @command="openBulk"><el-button :disabled="!checked.length||loading">批量修改{{checked.length?`（${checked.length}）`:''}} ▾</el-button><template #dropdown><el-dropdown-menu><el-dropdown-item command="supplier">供应商</el-dropdown-item><el-dropdown-item command="packaging">产品包装</el-dropdown-item><el-dropdown-item command="weight">产品重量</el-dropdown-item><el-dropdown-item command="cost">成本价</el-dropdown-item></el-dropdown-menu></template></el-dropdown><TableReset table-key="ProductPanel/table-1"/></div>
  <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon/>
  <template v-if="true"><el-table ref="productTable" @selection-change="(v:any[])=>checked=v" border data-width-key="ProductPanel/table-1" @header-dragend="(width: number, _old: number, column: any) => tableWidths.resize('table-1', width, column)" @header-contextmenu="(column: any, event: MouseEvent) => tableWidths.resetColumn('table-1', column, event)" :data="rows" v-loading="loading" row-key="id" empty-text="没有符合条件的标品" class="catalog-table">
   <el-table-column v-if="admin&&!filter.archived" type="selection" width="45"/>
   <el-table-column :column-key="'column-1'" :width="tableWidths.width('table-1', 'column-1', undefined)" label="标品编码 / 品名" min-width="230"><template #default="{row}"><button class="text-link" @click="edit(row)">{{row.code}}</button><CopyValue label="标品编码" :value="row.code"/><div>{{row.name}}</div><small v-if="row.specification">{{row.specification}}</small><small v-if="row.aliases?.length" :title="row.aliases.join(' / ')">原编码：<span v-for="code in row.aliases" :key="code">{{code}}<CopyValue label="原编码" :value="code"/></span></small></template></el-table-column>
   <el-table-column :column-key="'column-2'" label="图片" :width="tableWidths.width('table-1', 'column-2', '105')"><template #default="{row}"><el-image v-if="row.image_url" :src="row.image_url" fit="contain" class="catalog-image" :preview-src-list="[row.image_url]" preview-teleported/><button v-else class="image-placeholder" @click="edit(row)">{{admin?'上传图片':'暂无图片'}}</button></template></el-table-column>
   <el-table-column :column-key="'column-3'" label="参考采购成本 (CNY)" :width="tableWidths.width('table-1', 'column-3', '150')" align="center"><template #default="{row}"><b>{{cost(row.cost_cny)}}</b></template></el-table-column>
   <el-table-column :column-key="'column-4'" label="包装长 × 宽 × 高 (cm)" align="center" :width="tableWidths.width('table-1', 'column-4', '185')"><template #default="{row}">{{[row.package_length_cm,row.package_width_cm,row.package_height_cm].map(v=>v==null?'—':Number(v)).join(' × ')}}</template></el-table-column>
   <el-table-column :column-key="'column-5'" label="单品毛重 (g)" :width="tableWidths.width('table-1', 'column-5', '115')" align="center"><template #default="{row}">{{row.gross_weight_g==null?'—':Number(row.gross_weight_g)}}</template></el-table-column>
   <el-table-column :column-key="'column-6'" label="头程·英国（¥15/kg）" :width="tableWidths.width('table-1', 'column-6', '165')" align="center"><template #default="{row}"><b>{{cost(row.freight_uk_effective)}}</b><small>{{row.freight_uk_cny!=null?'手动维护':'按毛重计算'}}</small></template></el-table-column>
   <el-table-column :column-key="'column-7'" label="头程·欧洲（¥20/kg）" :width="tableWidths.width('table-1', 'column-7', '165')" align="center"><template #default="{row}"><b>{{cost(row.freight_eu_effective)}}</b><small>{{row.freight_eu_cny!=null?'手动维护':'德国及其他欧洲站点'}}</small></template></el-table-column>
   <el-table-column :column-key="'column-8'" :width="tableWidths.width('table-1', 'column-8', undefined)" label="供应商" min-width="195"><template #default="{row}">{{row.primary_supplier||row.suppliers[0]||'未维护'}}<small v-if="row.suppliers.length>1">共 {{row.suppliers.length}} 家供应商</small></template></el-table-column>
   <el-table-column :column-key="'column-9'" label="已关联 SKU" :width="tableWidths.width('table-1', 'column-9', '100')"><template #default="{row}"><el-button link type="primary" @click="showLinks(row)">{{row.linked_count}} 项</el-button></template></el-table-column>
   <el-table-column column-key="created_at" label="创建时间" :width="tableWidths.width('table-1','created_at',180)"><template #default="{row}">{{time(row.created_at)}}</template></el-table-column>
   <el-table-column column-key="created_by" label="创建人" :width="tableWidths.width('table-1','created_by',120)"><template #default="{row}">{{row.created_by||'未记录'}}</template></el-table-column>
   <el-table-column :column-key="'column-10'" label="操作" :width="tableWidths.width('table-1', 'column-10', '230')" fixed="right"><template #default="{row}"><el-button v-if="admin&&!row.archived" link type="primary" @click="emit('copy-product',row.id)">复制</el-button><el-button link type="primary" @click="edit(row)">{{admin&&!row.archived?'编辑':'查看'}}</el-button><el-button v-if="admin" :disabled="!!row.merged_into" link :type="row.archived?'primary':'danger'" @click="archiveProduct(row,!!row.archived)">{{row.merged_into?'已归并':row.archived?'恢复':'删除'}}</el-button><el-button link type="primary" @click="logRow=row">日志</el-button></template></el-table-column>
  </el-table></template>
  <div class="catalog-footer"><small>标品档案跨店铺共用；关联数仅统计你有权限的店铺。</small><AppPagination v-model:current-page="page" :total="total" @change="load" v-model:page-size="pageSize"/></div>
 </section>
 </template><el-dialog v-model="linkedDialog" :title="focusedProduct+' · 已关联 Listing'" width="780px"><p>关联操作请在 Listing 列表进行。</p><template v-if="true"><TableReset table-key="ProductPanel/table-3"/><el-table border data-width-key="ProductPanel/table-3" @header-dragend="(width: number, _old: number, column: any) => tableWidths.resize('table-3', width, column)" @header-contextmenu="(column: any, event: MouseEvent) => tableWidths.resetColumn('table-3', column, event)" :data="linkedRows" v-loading="linkedBusy"><el-table-column :column-key="'sku'" :width="tableWidths.width('table-3', 'sku', undefined)" prop="sku" label="亚马逊 SKU"><template #default="{row}">{{row.sku}}<CopyValue label="SKU" :value="row.sku"/></template></el-table-column><el-table-column :column-key="'store'" prop="store" label="店铺" :width="tableWidths.width('table-3', 'store', '150')"/><el-table-column :column-key="'marketplace'" prop="marketplace" label="站点" :width="tableWidths.width('table-3', 'marketplace', '90')"/><el-table-column :column-key="'asin'" prop="asin" label="ASIN" :width="tableWidths.width('table-3', 'asin', '150')"/></el-table></template><AppPagination v-model:current-page="linkedPage" :total="linkedTotal" @change="loadLinks" v-model:page-size="linkedPageSize"/></el-dialog>
<ProductBulkDialog v-if="bulkKind" :rows="bulkRows" :kind="bulkKind" :suppliers="options.suppliers" :suppliers-loading="suppliersLoading" @refresh-suppliers="refreshSuppliers()" @close="bulkKind=''" @saved="bulkSaved"/><ProductLog v-if="logRow" :row="logRow" @close="logRow=null"/>
</div>
</template>
<style scoped>
.catalog-image{width:70px;height:70px;border:1px solid #e6ede9;border-radius:8px;background:#fff}.catalog-image.large{width:110px;height:110px}.image-placeholder{border:1px dashed #bdcec5;color:#638a77;background:#f5f9f7;border-radius:8px;padding:16px 8px;cursor:pointer}.image-editor{display:flex;flex-wrap:wrap;gap:12px;align-items:center}.image-editor small{width:100%;color:#89998f}

.catalog-intro{background:#eaf4ef;border:1px solid #dce9e2;border-radius:12px;padding:22px 26px;display:flex;justify-content:space-between;align-items:center;color:#386653}.catalog-intro p{margin:8px 0 0;font-size:13px;color:#788f82}.catalog-nav{display:flex;justify-content:space-between;margin:24px 0 18px}.catalog-stats{display:flex;gap:30px;align-items:center;color:#7b8d83;font-size:13px;margin:0 0 20px}.catalog-stats b{font-size:22px;color:#365e4b;margin-left:7px}.catalog-stats small{margin-left:auto}.catalog-card{background:white;border:1px solid #e1e9e4;border-radius:12px;overflow:hidden}.catalog-filters{padding:22px;display:flex;gap:12px;align-items:center;flex-wrap:wrap}.catalog-filters>.el-input{flex:1;min-width:230px}.catalog-filters>.el-select{width:170px}.catalog-filters .el-button+.el-button{margin-left:0}.link-toolbar{display:flex;gap:14px;align-items:center;padding:0 22px 16px;font-size:12px;color:#829289}.link-toolbar>span{flex:1}.catalog-table :deep(th){background:#f7faf8;font-size:12px;color:#86968d}.catalog-table :deep(td){padding:10px 0;font-size:13px;color:#465b50}.catalog-table :deep(.cell){line-height:1.6}.catalog-table small,.audit-row small{display:block;color:#89988f;font-size:12px}.text-link{background:none;border:none;padding:0;color:#27745b;font-weight:650;font-size:13px;text-align:left;cursor:pointer}.listing-cell{display:flex;align-items:center;gap:12px;min-width:0}.listing-cell>div{min-width:0}.listing-cell img{width:46px;height:46px;object-fit:contain;border:1px solid #eff2ef;border-radius:8px}.clip-title{max-width:420px;overflow:hidden;white-space:nowrap;text-overflow:ellipsis;color:#89988f;font-size:12px}.catalog-footer{display:flex;align-items:center;justify-content:space-between;padding:18px 22px;gap:20px}.catalog-footer small{color:#89988f;font-size:12px}.product-form .el-select{width:100%}.form-two{display:grid;grid-template-columns:1fr 1fr;gap:18px}.field-hint{font-size:12px;color:#89988f;margin-left:12px}.source-box{padding:16px;background:#f5f8f6;border-radius:8px;font-size:12px;color:#819187;overflow-wrap:anywhere;margin-bottom:18px}.audit-row{padding:10px 0;border-bottom:1px solid #eff2ef;font-size:13px}.audit-row pre{white-space:pre-wrap;font-size:11px;overflow-wrap:anywhere}
</style>

<style scoped>.catalog-filters{display:flex;gap:10px;align-items:center;flex-wrap:wrap}.catalog-filters>.el-input{flex:none;width:260px}.catalog-filters>.el-select{flex:none;width:180px}.catalog-nav{justify-content:flex-start}</style>
