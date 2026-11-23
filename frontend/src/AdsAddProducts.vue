<script setup lang="ts">
import {ref,computed,onMounted,onUnmounted} from 'vue'
import {api} from './session'
import {submitAdProducts} from './adsSearchJobs'
import {ElMessage} from 'element-plus'
const props=defineProps<{profileId:string,campaignId?:string,groupId?:string}>(),emit=defineEmits(['close','done'])
const campaign=ref(props.campaignId||''),group=ref(props.groupId||''),options=ref<any>({campaigns:[],groups:[]}),q=ref(''),page=ref(1),total=ref(0),rows=ref<any[]>([]),selected=ref<any[]>([]),busy=ref(false)
const productsLoading=ref(false),scope=ref({store:'',country:''}),cache=new Map<string,any>();let request=0
const groups=computed(()=>options.value.groups.filter((g:any)=>g.campaign_id===campaign.value))
let closed=false
async function load(){const id=++request,key=JSON.stringify([q.value.trim(),page.value]);productsLoading.value=true;rows.value=[];try{let r=cache.get(key);if(!r){r=await api('/advertising/create/products?'+new URLSearchParams({profile_id:props.profileId,q:q.value.trim(),page:String(page.value),page_size:'10',compact:'true'}));if(cache.size>=20)cache.delete(cache.keys().next().value!);cache.set(key,r)}if(!closed&&id===request){rows.value=r.rows;total.value=r.total;scope.value={store:r.store,country:r.country}}}catch(e){if(!closed&&id===request)ElMessage.error((e as Error).message)}finally{if(id===request)productsLoading.value=false}}

function choose(row:any,value:any){selected.value=selected.value.filter(r=>r.sku!==row.sku);if(value)selected.value.push(row)}
function submit(){
 if(busy.value||!group.value||!campaign.value||!selected.value.length)return
 if(selected.value.length>100){ElMessage.warning('一次最多添加100个商品');return}
 busy.value=true
 const label='添加广告商品（'+selected.value.length+' 项）'
 if(submitAdProducts({profile_id:props.profileId,campaign_id:campaign.value,adgroup_id:group.value,products:selected.value.map(r=>({sku:r.sku,asin:r.asin||''}))},label))emit('close')
 else busy.value=false
}
onMounted(()=>{void load();void api('/advertising/create/options?profile_id='+encodeURIComponent(props.profileId)).then(r=>{if(!closed)options.value=r}).catch(e=>{if(!closed)ElMessage.error((e as Error).message)})})
onUnmounted(()=>{closed=true})
</script>
<template><el-dialog :model-value="true" title="添加广告商品" width="min(1100px, 94vw)" @close="emit('close')" :close-on-click-modal="false"><div v-loading="busy">
<p class="scope">{{scope.store}} · {{scope.country}}</p><div class="pickers"><el-select v-model="campaign" placeholder="选择广告活动" filterable @change="group=''" style="width:48%"><el-option v-for="c in options.campaigns" :key="c.id" :label="c.name" :value="c.id"/></el-select><el-select v-model="group" placeholder="选择广告组" filterable style="width:48%"><el-option v-for="g in groups" :key="g.id" :label="g.name" :value="g.id"/></el-select></div><el-input v-model="q" placeholder="搜索 SKU / 品名 / ASIN / FNSKU" @keyup.enter="page=1;load()"><template #append><el-button @click="page=1;load()">搜索</el-button></template></el-input><el-table :data="rows" v-loading="productsLoading" max-height="510" class="ads-product-picker"><el-table-column width="45"><template #default="{row}"><el-checkbox :model-value="selected.some(r=>r.sku===row.sku)" @change="(v:any)=>choose(row,v)"/></template></el-table-column><el-table-column label="图片" width="70"><template #default="{row}"><img v-if="row.image" class="product-image" :src="row.image" loading="lazy"/><span v-else>—</span></template></el-table-column><el-table-column prop="sku" label="SKU" min-width="220" show-overflow-tooltip/><el-table-column label="品名" min-width="180" show-overflow-tooltip><template #default="{row}">{{row.product_name||'未关联标品'}}</template></el-table-column><el-table-column prop="asin" label="ASIN" width="145"/><el-table-column label="FNSKU" width="145"><template #default="{row}">{{row.fnsku||'—'}}</template></el-table-column></el-table><el-pagination v-model:current-page="page" :page-size="10" :total="total" @current-change="load" layout="prev,pager,next"/><p>已选择 {{selected.length}} 个商品（最多100个）；提交后以启用状态添加，进度可在“广告任务”查看。</p>
</div><template #footer><el-button @click="emit('close')">关闭</el-button><el-button type="primary" :disabled="!campaign||!group||!selected.length||selected.length>100" :loading="busy" @click="submit">提交添加</el-button></template></el-dialog></template>
<style scoped>.pickers{display:flex;gap:12px;margin-bottom:14px}.scope{margin:0 0 12px;color:#6c7f77}.product-image{display:block;width:42px;height:42px;object-fit:contain}.ads-product-picker{margin-top:12px}</style>
