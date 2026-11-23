<script setup lang="ts">
import {ref} from 'vue'
import {ElMessage,ElMessageBox} from 'element-plus'
import {api} from './session'
import {useTableWidths} from './tableWidths'
import ShippingProductPicker from './ShippingProductPicker.vue'
const widths=useTableWidths('ShippingPackingEditor')
const props=defineProps<{plan:any}>(),emit=defineEmits(['close','saved'])
const busy=ref(false),error=ref('')
const boxes=ref(props.plan.packing.boxes.map((b:any)=>({...b,items:b.items.filter((i:any)=>i.quantity>0).map((i:any)=>({...props.plan.items.find((p:any)=>p.sku===i.sku),...i}))})))
const additions=ref<any[]>([]),picking=ref(false),picked=ref<any[]>([]),pickerBusy=ref(false),targetBox=ref<any>(null),pickerError=ref('')
const metrics=[{key:'weight_kg',label:'重量 kg'},{key:'length_cm',label:'长 cm'},{key:'width_cm',label:'宽 cm'},{key:'height_cm',label:'高 cm'}]
function openAdd(box:any){targetBox.value=box;picked.value=[];pickerError.value='';picking.value=true}
function addToBox(){
 if(pickerBusy.value||!picked.value.length)return
 pickerError.value=''
 const lines=[]
 for(const item of picked.value){
  const listing=item.candidates.find((c:any)=>c.id===item.listing_id),quantity=Number(item.quantity)
  if(!listing||!/^[A-Z0-9]{10}$/.test(listing.fnsku||'')){pickerError.value='请选择当前店铺、国家的 SKU，并确保 FNSKU 已同步';return}
  if(!Number.isInteger(quantity)||quantity<1||quantity>100000){pickerError.value='新增数量须为 1 至 100000 的整数';return}
  const current=targetBox.value.items.find((i:any)=>i.sku===listing.sku)
  const original=props.plan.items.find((i:any)=>i.sku===listing.sku)?.quantity||0
  const pending=additions.value.filter(i=>i.listing_id===listing.id).reduce((n:number,i:any)=>n+i.quantity,0)
  if((current?.quantity||0)+quantity>100000||original+pending+quantity>100000){pickerError.value='累计数量不能超过 100000';return}
  lines.push({...item,...listing,listing_id:listing.id,quantity})
 }
 for(const item of lines){
  const existing=targetBox.value.items.find((i:any)=>i.sku===item.sku)
  if(existing)existing.quantity+=item.quantity
  else targetBox.value.items.push({sku:item.sku,fnsku:item.fnsku,name:item.name,quantity:item.quantity})
  const pending=additions.value.find(i=>i.listing_id===item.listing_id&&i.product_id===item.product_id)
  if(pending)pending.quantity+=item.quantity
  else additions.value.push({product_id:item.product_id,listing_id:item.listing_id,quantity:item.quantity})
 }
 picking.value=false
}
async function save(){if(busy.value)return;busy.value=true;error.value='';try{await api(`/shipping/${props.plan.id}/packing-edit`,{revision:props.plan.revision,additions:additions.value,boxes:boxes.value.map((b:any)=>({weight_kg:b.weight_kg,length_cm:b.length_cm,width_cm:b.width_cm,height_cm:b.height_cm,items:b.items.map((i:any)=>({sku:i.sku,quantity:i.quantity}))}))});ElMessage.success('装箱单及发货计划已保存');emit('saved')}catch(e){error.value=(e as Error).message}finally{busy.value=false}}
async function remove(){if(busy.value)return;try{await ElMessageBox.confirm('删除当前装箱单和装箱数量？删除后需要重新上传，历史文件和日志保留。','删除装箱单',{type:'warning'});busy.value=true;await api(`/shipping/${props.plan.id}/packing-delete`,{revision:props.plan.revision});ElMessage.success('装箱单已删除，请重新上传');emit('saved')}catch(e){if(e instanceof Error)error.value=e.message}finally{busy.value=false}}
</script>
<template>
<el-dialog :model-value="true" title="编辑装箱单" width="min(1100px,96vw)" :close-on-click-modal="false" :close-on-press-escape="!busy" :show-close="!busy" @close="emit('close')">
 <el-alert v-if="error" :title="error" type="error" :closable="false"/>
 <el-form :disabled="busy">
  <section v-for="(box,index) in boxes" :key="index" class="packing-edit-box">
   <div class="packing-box-heading"><h4>第 {{box.number}} 箱</h4><el-button type="primary" plain :disabled="busy" @click="openAdd(box)">添加发货计划</el-button></div>
   <div class="packing-metrics"><el-form-item v-for="m in metrics" :key="m.key" :label="m.label"><el-input-number v-model="box[m.key]" :min="0.001" :max="100000" :precision="3" :aria-label="`第${box.number}箱${m.label}`"/></el-form-item></div>
   <el-table data-width-key="ShippingPackingEditor/items" :data="box.items.filter((i:any)=>i.quantity!==0)" @header-dragend="(w:number,_old:number,c:any)=>widths.resize('items',w,c)" @header-contextmenu="(c:any,e:MouseEvent)=>widths.resetColumn('items',c,e)">
    <el-table-column :width="widths.width('items','sku',350)" column-key="sku" prop="sku" label="SKU / FNSKU"><template #default="{row}">{{row.sku}}<small>{{row.fnsku}}</small></template></el-table-column>
    <el-table-column :width="widths.width('items','name',350)" column-key="name" prop="name" label="品名"/>
    <el-table-column :width="widths.width('items','quantity',200)" column-key="quantity" label="装箱数量" align="center"><template #default="{row}"><el-input-number v-model="row.quantity" :min="0" :max="100000" :precision="0" :aria-label="`${row.sku}第${box.number}箱数量`"/></template></el-table-column>
   </el-table>
  </section>
 </el-form>
 <template #footer><el-button :disabled="busy" @click="emit('close')">取消</el-button><el-button type="danger" plain :disabled="busy" @click="remove">删除装箱单</el-button><el-button type="primary" :loading="busy" @click="save">保存</el-button></template>
 <el-dialog v-model="picking" title="添加发货计划" width="min(1100px,96vw)" append-to-body :close-on-click-modal="false">
  <p>商品添加到第 {{targetBox?.number}} 箱，同时增加当前发货计划数量；已有 SKU 累加，不存在的 SKU 新增。点击装箱单“保存”后生效。</p>
  <el-alert v-if="pickerError" :title="pickerError" type="error" :closable="false"/>
  <ShippingProductPicker v-if="picking" v-model="picked" :store="plan.store" :country="plan.country" :disabled="busy" :hide-reset="true" @busy="pickerBusy=$event"/>
  <template #footer><el-button @click="picking=false">取消</el-button><el-button type="primary" :disabled="pickerBusy||!picked.length" @click="addToBox">添加到本箱</el-button></template>
 </el-dialog>
</el-dialog>
</template>
<style scoped>.packing-edit-box{margin-bottom:22px}.packing-box-heading{display:flex;align-items:center;gap:18px;margin-bottom:16px}.packing-box-heading h4{margin:0}.packing-metrics{display:flex;gap:16px;flex-wrap:wrap}.packing-metrics .el-form-item{margin-bottom:12px}small{display:block;color:#8a9b92;font-size:12px;margin-top:4px}</style>
