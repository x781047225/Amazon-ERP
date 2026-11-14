<script setup lang="ts">
import {computed} from 'vue'
import ListingValue from './ListingValue.vue'
import type {Field,Scalar} from './listingTypes'
const props=defineProps<{field:Field,modelValue:Scalar[],disabled?:boolean}>()
const emit=defineEmits<{ 'update:modelValue':[value:Scalar[]] }>()
const long=computed(()=>['bullet_point','product_description','generic_keyword'].includes(props.field.key))
function update(index:number,value:Scalar){const list=[...props.modelValue];list[index]=value;emit('update:modelValue',list)}
function add(){emit('update:modelValue',[...props.modelValue,props.field.kind==='boolean'?false:['number','integer'].includes(props.field.kind)?0:''])}
function remove(index:number){emit('update:modelValue',props.modelValue.filter((_,i)=>i!==index))}
const count=(v:Scalar)=>Array.from(String(v)).length
</script>
<template>
 <div class="listing-field" :data-field="field.key"><div class="lf-head"><label>{{field.label}}</label><span class="lf-kind">{{field.editable?'可保存本地修改':'只读'}}</span></div>
 <template v-if="field.editable"><div v-for="(value,index) in modelValue" :key="index" class="lf-input-row"><div class="lf-input">
 <el-select v-if="field.enum.length" :model-value="value" :disabled="disabled" filterable @update:model-value="(v:Scalar)=>update(index,v)"><el-option v-for="(option,i) in field.enum" :key="String(option)" :label="field.enum_names[i]||String(option)" :value="option"/></el-select>
 <el-switch v-else-if="field.kind==='boolean'" :model-value="!!value" :disabled="disabled" @update:model-value="(v:string|number|boolean)=>update(index,!!v)"/>
 <el-input-number v-else-if="['number','integer'].includes(field.kind)" :model-value="Number(value)" :disabled="disabled" :min="field.minimum??undefined" :max="field.maximum??undefined" :precision="field.kind==='integer'?0:undefined" @update:model-value="(v:number|undefined)=>update(index,v??0)"/>
 <el-input v-else :model-value="String(value)" :type="long?'textarea':'text'" :autosize="long?{minRows:field.key==='product_description'?5:2,maxRows:12}:undefined" :disabled="disabled" @update:model-value="(v:Scalar)=>update(index,v)"/>
 <div v-if="field.kind==='string'" class="lf-count" :class="{over:field.max_length!==null&&count(value)>field.max_length}">{{modelValue.length>1?`第 ${index+1} 条 · `:''}}{{count(value)}} 字符{{field.max_length!==null?` / 模板上限 ${field.max_length}`:''}}</div>
 </div><el-button v-if="modelValue.length>Math.max(field.min_items,1)" link :disabled="disabled" @click="remove(index)">移除</el-button></div>
 <el-button v-if="modelValue.length<(field.max_items??20) && !['item_name','product_description','generic_keyword'].includes(field.key)" link type="primary" :disabled="disabled" @click="add">＋ 添加一条</el-button>
 </template>
 <ListingValue v-else :value="field.raw"/>
 <details v-if="field.description" class="lf-help"><summary>填写说明</summary><p>{{field.description}}</p></details>
 </div>
</template>
<style scoped>
.listing-field{padding:20px 0;border-bottom:1px solid #edf1ee}.listing-field:last-child{border-bottom:0}.lf-head{display:flex;justify-content:space-between;gap:12px;margin-bottom:11px}.lf-head label{font-size:13px;font-weight:600;color:#395648}.lf-kind{color:#99a69e;font-size:10px;white-space:nowrap}.lf-input-row{display:flex;gap:8px;align-items:flex-start;margin-bottom:7px}.lf-input{flex:1;min-width:0}.lf-input .el-select{width:100%}.lf-count{font-size:10px;color:#92a198;margin-top:6px;text-align:right}.lf-count.over{color:#b46645}.lf-help{color:#8a9a91;font-size:11px;line-height:1.7;margin-top:9px}.lf-help p{white-space:pre-wrap}.lf-help summary{cursor:pointer}
</style>
