<script setup lang="ts">
import {computed,ref} from 'vue'
import {ElMessageBox} from 'element-plus'
import {queuePrice} from './priceJobs'
import type {Listing} from './listingTypes'
const props=defineProps<{rows:Listing[]}>()
const emit=defineEmits<{close:[],saved:[],busy:[boolean]}>()
const items=ref(props.rows.map(row=>({row,price:'',status:'',error:''})))
const uniform=ref<Record<string,string>>({}),busy=ref(false),finished=ref(false),error=ref('')
const currencies=computed(()=>[...new Set(items.value.map(x=>x.row.pricing.currency!))])
const sent=computed(()=>items.value.filter(x=>x.status==='queued').length)
function valid(price:string){return /^\d+(\.\d{1,2})?$/.test(price)&&Number(price)>0&&Number(price)<=9999999.99}
function apply(currency:string){error.value='';const price=uniform.value[currency]||'';if(!valid(price)){error.value='请填写大于零、最多两位小数的售价';return}for(const item of items.value)if(item.row.pricing.currency===currency)item.price=price}
function validation(){for(const x of items.value){if(!valid(x.price))return `${x.row.sku}：请填写有效新售价，最多两位小数`;if(['active','scheduled'].includes(x.row.pricing.sale_status)&&x.row.pricing.sale_price&&Number(x.price)<=Number(x.row.pricing.sale_price))return `${x.row.sku}：新售价必须高于现有促销价`}return ''}
async function submit(){
 if(busy.value||finished.value)return
 error.value=validation();if(error.value)return
 try{await ElMessageBox.confirm(`确认提交所列 ${items.value.length} 个商品的新售价？促销价、促销时间及企业价保持不变。`,'确认批量调价',{confirmButtonText:'确认提交',cancelButtonText:'返回核对',type:'warning'})}catch{return}
 busy.value=true;emit('busy',true)
 try{for(const x of items.value){x.status='sending';const result=await queuePrice(x.row,{price:x.price,expected_currency:x.row.pricing.currency,expected_fingerprint:x.row.pricing.fingerprint},true);x.status=result.ok?'queued':'failed';x.error=result.error}}finally{busy.value=false;finished.value=true;emit('busy',false)}
}
async function close(){if(busy.value)return;if(!finished.value&&items.value.some(x=>x.price)){try{await ElMessageBox.confirm('新售价尚未提交，确定关闭？','关闭批量调价',{confirmButtonText:'放弃并关闭',cancelButtonText:'继续编辑'})}catch{return}}if(finished.value)emit('saved');emit('close')}
</script>
<template>
 <el-dialog :model-value="true" title="批量调价 · 仅售价" width="960px" class="bulk-price-dialog" :before-close="close" :close-on-click-modal="false" :close-on-press-escape="!busy" :show-close="!busy" destroy-on-close>
  <p>已选择 {{items.length}} 个商品，请核对店铺、站点、币种和新售价。仅修改普通售价，促销价及时间、企业价保持不变。</p>
  <el-alert v-if="error" :title="error" type="error" :closable="false"/>
  <div v-if="!finished" class="bulk-price-fill"><div v-for="currency in currencies" :key="currency"><span>{{currency}} 统一填价</span><el-input v-model="uniform[currency]" :aria-label="currency+'统一售价'" inputmode="decimal" :disabled="busy" placeholder="新售价"/><el-button :disabled="busy" @click="apply(currency)">应用到 {{currency}} 商品</el-button></div></div>
  <el-table :data="items" border max-height="430" row-key="row.id">
   <el-table-column label="商品" min-width="210"><template #default="{row:x}"><strong>{{x.row.sku}}</strong><div class="muted">{{x.row.asin}}</div></template></el-table-column>
   <el-table-column label="店铺 / 站点" min-width="135"><template #default="{row:x}">{{x.row.store}} · {{x.row.marketplace}}</template></el-table-column>
   <el-table-column label="币种" width="75"><template #default="{row:x}">{{x.row.pricing.currency}}</template></el-table-column>
   <el-table-column label="当前售价" width="100"><template #default="{row:x}">{{x.row.pricing.price??'—'}}</template></el-table-column>
   <el-table-column label="新售价" min-width="140"><template #default="{row:x}"><el-input v-model="x.price" :aria-label="x.row.sku+'新售价'" inputmode="decimal" :disabled="busy||finished" placeholder="填写新售价"/></template></el-table-column>
   <el-table-column label="提交状态" min-width="180"><template #default="{row:x}"><el-tag v-if="x.status" :type="x.status==='failed'?'danger':x.status==='queued'?'success':'info'">{{x.status==='queued'?'已排队':x.status==='sending'?'提交中':'未完成，请核对'}}</el-tag><span v-else>待确认</span><p v-if="x.error" class="item-error">{{x.error}}</p></template></el-table-column>
  </el-table>
  <p v-if="finished">已排队 {{sent}} 项，未完成 {{items.length-sent}} 项。后台处理结果会在列表和商品提交记录中更新；连接异常的商品请先核对记录。</p>
  <template #footer><el-button :disabled="busy" @click="close">{{finished?'关闭':'取消'}}</el-button><el-button v-if="!finished" type="primary" :loading="busy" @click="submit">提交 {{items.length}} 项售价</el-button></template>
 </el-dialog>
</template>
<style scoped>.bulk-price-fill{display:flex;flex-wrap:wrap;gap:12px;margin:16px 0}.bulk-price-fill>div{display:flex;align-items:center;gap:8px}.bulk-price-fill .el-input{width:130px}.item-error{font-size:12px;color:#c45656;white-space:normal;overflow-wrap:anywhere;margin:6px 0}.muted{font-size:12px;color:#89978e}</style>
