<script setup lang="ts">
import {ref,computed,onMounted,onUnmounted,watch} from 'vue'
import {api} from './session'
import {submitAdProducts} from './adsSearchJobs'
import {ElMessage} from 'element-plus'
const props=defineProps<{profileId:string,campaignId?:string,groupId?:string}>(),emit=defineEmits(['close','done'])
const campaign=ref(props.campaignId||''),group=ref(props.groupId||''),options=ref<any>({campaigns:[],groups:[]}),q=ref(''),page=ref(1),total=ref(0),rows=ref<any[]>([]),selected=ref<any[]>([]),busy=ref(false)
const productsLoading=ref(false),scope=ref({store:'',country:''}),existing=ref(new Set<string>()),loadError=ref('');let request=0
const groups=computed(()=>options.value.groups.filter((g:any)=>g.campaign_id===campaign.value))
let closed=false
async function load(){const id=++request;productsLoading.value=true;loadError.value='';rows.value=[];try{const r=await api('/advertising/create/products?'+new URLSearchParams({profile_id:props.profileId,adgroup_id:group.value,q:q.value.trim(),page:String(page.value),page_size:'10',compact:'true'}));if(!closed&&id===request){existing.value=new Set(r.existing_skus||[]);selected.value=selected.value.filter(r=>!existing.value.has(r.sku));rows.value=r.rows;total.value=r.total;scope.value={store:r.store,country:r.country}}}catch(e){if(!closed&&id===request){loadError.value=(e as Error).message;ElMessage.error(loadError.value)}}finally{if(id===request)productsLoading.value=false}}
const selectable=computed(()=>rows.value.filter(r=>!existing.value.has(r.sku)))
const checkedCount=computed(()=>selectable.value.filter(r=>selected.value.some(s=>s.sku===r.sku)).length)
function choose(row:any,value:any){if(!group.value||productsLoading.value||existing.value.has(row.sku))return;selected.value=selected.value.filter(r=>r.sku!==row.sku);if(value){if(selected.value.length>=100){ElMessage.warning('一次最多添加100个商品');return}selected.value.push(row)}}
function choosePage(value:any){for(const row of selectable.value)choose(row,value)}
watch(group,()=>{selected.value=[];existing.value=new Set();void load()})

function submit(){
 if(busy.value||productsLoading.value||loadError.value||!group.value||!campaign.value||!selected.value.length)return
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
<p class="scope">{{scope.store}} · {{scope.country}}</p><div class="pickers"><el-select v-model="campaign" placeholder="选择广告活动" filterable @change="group=''" style="width:48%"><el-option v-for="c in options.campaigns" :key="c.id" :label="c.name" :value="c.id"/></el-select><el-select v-model="group" placeholder="选择广告组" filterable style="width:48%"><el-option v-for="g in groups" :key="g.id" :label="g.name" :value="g.id"/></el-select></div><el-input v-model="q" placeholder="搜索 SKU / 品名 / ASIN / FNSKU" @keyup.enter="page=1;load()"><template #append><el-button @click="page=1;load()">搜索</el-button></template></el-input><el-table :data="rows" v-loading="productsLoading" max-height="510" class="ads-product-picker"><el-table-column width="55"><template #header><el-checkbox aria-label="全选当前页可添加商品" :disabled="!group||productsLoading||!!loadError||!selectable.length" :model-value="selectable.length>0&&checkedCount===selectable.length" :indeterminate="checkedCount>0&&checkedCount<selectable.length" @change="choosePage"/></template><template #default="{row}"><el-checkbox :aria-label="'选择 '+row.sku" :disabled="!group||productsLoading||!!loadError||existing.has(row.sku)" :model-value="selected.some(r=>r.sku===row.sku)" @change="(v:any)=>choose(row,v)"/></template></el-table-column><el-table-column label="图片" width="70"><template #default="{row}"><img v-if="row.image" class="product-image" :src="row.image" loading="lazy"/><span v-else>—</span></template></el-table-column><el-table-column prop="sku" label="SKU" min-width="220" show-overflow-tooltip/><el-table-column label="品名" min-width="180" show-overflow-tooltip><template #default="{row}">{{row.product_name||'未关联标品'}}</template></el-table-column><el-table-column label="当前广告组" width="140"><template #default="{row}"><el-tag v-if="group&&existing.has(row.sku)" type="info">已存在</el-tag><span v-else>{{group?'可添加':'请先选择广告组'}}</span></template></el-table-column><el-table-column prop="asin" label="ASIN" width="145"/><el-table-column label="FNSKU" width="145"><template #default="{row}">{{row.fnsku||'—'}}</template></el-table-column></el-table><el-pagination v-model:current-page="page" :page-size="10" :total="total" @current-change="load" layout="prev,pager,next"/><p>顶部全选仅选择当前页可添加商品；已存在的商品不会重复添加。</p><p>已选择 {{selected.length}} 个商品（最多100个）；提交后以启用状态添加，进度可在“广告任务”查看。</p>
</div><template #footer><el-button @click="emit('close')">关闭</el-button><el-button type="primary" :disabled="productsLoading||!!loadError||!campaign||!group||!selected.length||selected.length>100" :loading="busy" @click="submit">提交添加</el-button></template></el-dialog></template>
<style scoped>.pickers{display:flex;gap:12px;margin-bottom:14px}.scope{margin:0 0 12px;color:#6c7f77}.product-image{display:block;width:42px;height:42px;object-fit:contain}.ads-product-picker{margin-top:12px}</style>
