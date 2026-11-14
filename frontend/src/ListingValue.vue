<script setup lang="ts">
defineProps<{value:unknown}>()
const labels:Record<string,string>={value:'值',unit:'单位',currency:'币种',amount:'金额',price:'价格',quantity:'数量',audience:'适用对象',start_at:'开始时间',end_at:'结束时间',marketplaceId:'站点',marketplace_id:'站点',language_tag:'语言',fulfillmentChannelCode:'配送渠道',fulfillment_channel_code:'配送渠道',conditionType:'商品状况',offerType:'报价类型',our_price:'售价',schedule:'生效安排',value_with_tax:'含税金额',height:'高度',length:'长度',width:'宽度',parent_sku:'父 SKU',child_relationship_type:'关系',type:'类型',status:'状态'}
const entries=(v:unknown)=>Object.entries(v as Record<string,unknown>).filter(([k])=>k!=='marketplace_id'&&k!=='marketplaceId')
const scalar=(v:unknown)=>typeof v==='boolean'?(v?'是':'否'):v==null?'—':String(v)
</script>
<template>
 <div v-if="Array.isArray(value)" class="lv-array"><div v-for="(part,i) in value" :key="i" class="lv-entry"><ListingValue :value="part"/></div><span v-if="!value.length" class="muted">暂无</span></div>
 <div v-else-if="value!==null && typeof value==='object'" class="lv-object"><div v-for="([key,part]) in entries(value)" :key="key" class="lv-pair"><span v-if="key!=='value'" class="lv-label">{{labels[key]||key.replaceAll('_',' ')}}</span><ListingValue :value="part"/></div></div>
 <span v-else class="lv-text">{{scalar(value)}}</span>
</template>
<style scoped>
.lv-text{white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.7;font-size:12px}.lv-array{display:grid;gap:9px}.lv-entry+.lv-entry{border-top:1px dashed #e6ece8;padding-top:8px}.lv-pair{display:flex;gap:10px;align-items:baseline;min-width:0}.lv-pair>.lv-object,.lv-pair>.lv-array{flex:1}.lv-label{flex-shrink:0;color:#86978d;font-size:10px;min-width:48px}.lv-object{display:grid;gap:4px}
</style>
