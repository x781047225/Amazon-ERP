<script setup lang="ts">
import {computed,ref} from 'vue'
import {ElMessageBox} from 'element-plus'
import {queuePrice} from './priceJobs'
import type {Listing} from './listingTypes'
const props=defineProps<{rows:Listing[]}>()
const emit=defineEmits<{close:[],saved:[],busy:[boolean]}>()
const items=ref(props.rows.map(row=>({row,price:'',sale_price:'',dates:null as Date[]|null,status:'',error:''})))
const mode=ref<'price'|'promotion'|'both'>('price')
const withPrice=computed(()=>mode.value!=='promotion'),withPromotion=computed(()=>mode.value!=='price')
const modeLabel=computed(()=>({price:'仅售价',promotion:'仅促销',both:'售价＋促销'})[mode.value])
const uniform=ref<Record<string,string>>({}),uniformSale=ref<Record<string,string>>({}),uniformDates=ref<Date[]|null>(null)
const busy=ref(false),finished=ref(false),error=ref('')
const timezone=Intl.DateTimeFormat().resolvedOptions().timeZone
const currencies=computed(()=>[...new Set(items.value.map(x=>x.row.pricing.currency!))])
const sent=computed(()=>items.value.filter(x=>x.status==='queued').length)
const changed=computed(()=>items.value.some(x=>x.price||x.sale_price||x.dates?.length)||Object.values(uniform.value).some(Boolean)||Object.values(uniformSale.value).some(Boolean)||!!uniformDates.value?.length)
function valid(price:string){return /^\d+(\.\d{1,2})?$/.test(price)&&Number(price)>0&&Number(price)<=9999999.99}
function apply(currency:string,promotion=false){error.value='';const price=(promotion?uniformSale.value:uniform.value)[currency]||'';if(!valid(price)){error.value='请填写大于零、最多两位小数的价格';return}for(const item of items.value)if(item.row.pricing.currency===currency)item[promotion?'sale_price':'price']=price}
function datesError(dates:Date[]|null){if(dates?.length!==2||dates.some(d=>!Number.isFinite(d.getTime())))return '请填写完整促销开始和结束时间';if(dates[1]<=dates[0])return '促销结束时间必须晚于开始时间';if(dates[1].getTime()<=Date.now())return '促销结束时间必须晚于当前时间';return ''}
function applyDates(){error.value=datesError(uniformDates.value);if(error.value)return;for(const item of items.value)item.dates=uniformDates.value!.map(d=>new Date(d))}
function validation(){for(const x of items.value){
 if(withPrice.value&&!valid(x.price))return `${x.row.sku}：请填写有效新售价，最多两位小数`
 if(withPromotion.value){
  if(!valid(x.sale_price))return `${x.row.sku}：请填写有效促销价，最多两位小数`
  const issue=datesError(x.dates);if(issue)return `${x.row.sku}：${issue}`
  const base=withPrice.value?x.price:x.row.pricing.price
  if(!base||(!withPrice.value&&x.row.pricing.price_source!=='attribute'))return `${x.row.sku}：缺少常规售价，请同步后再设置促销`
  if(Number(x.sale_price)>=Number(base))return `${x.row.sku}：促销价必须低于${withPrice.value?'新':'当前'}售价`
 }else if(['active','scheduled'].includes(x.row.pricing.sale_status)&&x.row.pricing.sale_price&&Number(x.price)<=Number(x.row.pricing.sale_price))return `${x.row.sku}：新售价必须高于现有促销价`
}return ''}
async function submit(){
 if(busy.value||finished.value)return
 error.value=validation();if(error.value)return
 try{await ElMessageBox.confirm(`确认提交所列 ${items.value.length} 个商品的${modeLabel.value}设置？${withPromotion.value?`促销时间按 ${timezone} 填写，将覆盖原促销计划。`: '促销价和促销时间保持不变。'}`,'确认批量调价',{confirmButtonText:'确认提交',cancelButtonText:'返回核对',type:'warning'})}catch{return}
 busy.value=true;emit('busy',true)
 try{for(const x of items.value){x.status='sending';const body={expected_currency:x.row.pricing.currency,expected_fingerprint:x.row.pricing.fingerprint,...(withPrice.value?{price:x.price}:{}),...(withPromotion.value?{sale_price:x.sale_price,sale_start:x.dates![0].toISOString(),sale_end:x.dates![1].toISOString()}: {})};const result=await queuePrice(x.row,body,!withPromotion.value,withPromotion.value);x.status=result.ok?'queued':'failed';x.error=result.error}}finally{busy.value=false;finished.value=true;emit('busy',false)}
}
async function close(){if(busy.value)return;if(!finished.value&&changed.value){try{await ElMessageBox.confirm('调价内容尚未提交，确定关闭？','关闭批量调价',{confirmButtonText:'放弃并关闭',cancelButtonText:'继续编辑'})}catch{return}}if(finished.value)emit('saved');emit('close')}
function time(value:string|null){return value?new Date(value).toLocaleString('zh-CN',{hour12:false}):'—'}
</script>
<template>
 <el-dialog :model-value="true" title="批量调价" width="min(1420px, 96vw)" class="bulk-price-dialog" :before-close="close" :close-on-click-modal="false" :close-on-press-escape="!busy" :show-close="!busy" destroy-on-close>
  <div class="bulk-mode"><span>调整内容</span><el-radio-group v-model="mode" :disabled="busy||finished" @change="error=''" aria-label="调整内容"><el-radio-button value="price">仅售价</el-radio-button><el-radio-button value="promotion">仅促销</el-radio-button><el-radio-button value="both">售价＋促销</el-radio-button></el-radio-group><span>已选择 {{items.length}} 个商品</span></div>
  <p class="muted">{{mode==='promotion'?'仅更新促销价和时间，常规售价保持不变。':mode==='price'?'仅更新常规售价，促销计划保持不变。':'同时更新常规售价和促销计划。'}} 企业价保持不变。</p>
  <el-alert v-if="error" :title="error" type="error" :closable="false"/>
  <div v-if="!finished" class="bulk-price-fill">
   <div v-for="currency in currencies" :key="currency" class="fill-row"><strong>{{currency}}</strong><template v-if="withPrice"><el-input v-model="uniform[currency]" :aria-label="currency+'统一售价'" inputmode="decimal" :disabled="busy" placeholder="统一售价"/><el-button :disabled="busy" @click="apply(currency)">应用售价</el-button></template><template v-if="withPromotion"><el-input v-model="uniformSale[currency]" :aria-label="currency+'统一促销价'" inputmode="decimal" :disabled="busy" placeholder="统一促销价"/><el-button :disabled="busy" @click="apply(currency,true)">应用促销价</el-button></template></div>
   <div v-if="withPromotion" class="fill-row dates-fill"><span>统一促销时间</span><el-date-picker v-model="uniformDates" type="datetimerange" start-placeholder="统一开始时间" end-placeholder="统一结束时间" format="YYYY-MM-DD HH:mm" :disabled="busy"/><el-button :disabled="busy" @click="applyDates">应用到全部商品</el-button><span class="muted">时间按 {{timezone}} 填写</span></div>
  </div>
  <el-table :data="items" border max-height="430" row-key="row.id">
   <el-table-column label="商品" min-width="170"><template #default="{row:x}"><strong>{{x.row.sku}}</strong><div class="muted">{{x.row.asin}}</div></template></el-table-column>
   <el-table-column label="店铺 / 站点" min-width="125"><template #default="{row:x}">{{x.row.store}} · {{x.row.marketplace}}</template></el-table-column>
   <el-table-column label="币种" width="70"><template #default="{row:x}">{{x.row.pricing.currency}}</template></el-table-column>
   <el-table-column label="当前售价 / 促销价" width="145" align="center"><template #default="{row:x}">{{x.row.pricing.price??'—'}} / {{x.row.pricing.sale_price??'—'}}<div v-if="withPromotion&&x.row.pricing.sale_price" class="muted">{{time(x.row.pricing.sale_start)}}<br>至 {{time(x.row.pricing.sale_end)}}</div></template></el-table-column>
   <el-table-column v-if="withPrice" label="新售价" min-width="115" align="center"><template #default="{row:x}"><el-input v-model="x.price" :aria-label="x.row.sku+'新售价'" inputmode="decimal" :disabled="busy||finished" placeholder="新售价"/></template></el-table-column>
   <el-table-column v-if="withPromotion" label="新促销价" min-width="115" align="center"><template #default="{row:x}"><el-input v-model="x.sale_price" :aria-label="x.row.sku+'新促销价'" inputmode="decimal" :disabled="busy||finished" placeholder="新促销价"/></template></el-table-column>
   <el-table-column v-if="withPromotion" label="促销开始 / 结束时间" width="360"><template #default="{row:x}"><el-date-picker v-model="x.dates" type="datetimerange" :start-placeholder="x.row.sku+'开始时间'" :end-placeholder="x.row.sku+'结束时间'" format="YYYY-MM-DD HH:mm" :disabled="busy||finished" style="width:100%"/></template></el-table-column>
   <el-table-column label="提交状态" min-width="150"><template #default="{row:x}"><el-tag v-if="x.status" :type="x.status==='failed'?'danger':x.status==='queued'?'success':'info'">{{x.status==='queued'?'已排队':x.status==='sending'?'提交中':'未完成，请核对'}}</el-tag><span v-else>待确认</span><p v-if="x.error" class="item-error">{{x.error}}</p></template></el-table-column>
  </el-table>
  <p v-if="finished">已排队 {{sent}} 项，未完成 {{items.length-sent}} 项。后台处理结果会在列表和商品提交记录中更新；连接异常的商品请先核对记录。</p>
  <template #footer><el-button :disabled="busy" @click="close">{{finished?'关闭':'取消'}}</el-button><el-button v-if="!finished" type="primary" :loading="busy" @click="submit">提交 {{items.length}} 项调价</el-button></template>
 </el-dialog>
</template>
<style scoped>.bulk-mode,.fill-row{display:flex;align-items:center;flex-wrap:wrap;gap:12px}.bulk-price-fill{display:flex;flex-direction:column;gap:12px;margin:16px 0;padding:16px;background:#f5f8f6;border-radius:8px}.fill-row>strong{width:44px}.bulk-price-fill .el-input{width:130px}.dates-fill :deep(.el-date-editor){max-width:390px;flex:none}.item-error{font-size:12px;color:#c45656;white-space:normal;overflow-wrap:anywhere;margin:6px 0}.muted{font-size:12px;color:#89978e;line-height:1.6}</style>
