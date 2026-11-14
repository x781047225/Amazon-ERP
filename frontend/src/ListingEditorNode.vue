<script setup lang="ts">
import {computed} from 'vue'
import type {EditorSchema} from './listingTypes'
const props=defineProps<{schema:EditorSchema,modelValue:any,baseline:any,path:string,disabled?:boolean}>()
const emit=defineEmits<{'update:modelValue':[value:any]}>()
const modified=computed(()=>JSON.stringify(props.modelValue)!==JSON.stringify(props.baseline))
const items=computed<any[]>(()=>Array.isArray(props.modelValue)?props.modelValue:[])
const fields=computed(()=>Object.entries(props.schema.properties||{}).filter(([k,n])=>!['marketplace_id','language_tag'].includes(k)&&!n.hidden))
const options=computed(()=>props.schema.enum||props.schema.anyOf?.find(n=>n.enum)?.enum||[])
const suggested=computed(()=>!props.schema.enum&&!!props.schema.anyOf?.some(n=>!n.enum&&n.type==='string'))
const enumNames=computed(()=>props.schema.enumNames||props.schema.anyOf?.find(n=>n.enum)?.enumNames||[])
const isLong=computed(()=>['bullet_point','product_description','generic_keyword','title_differentiation'].some(k=>props.path.startsWith(k)))
const count=computed(()=>Array.from(String(props.modelValue??'').replace(props.path.startsWith('product_description')?/<[^>]*>/g:/$^/g,'')).length)
const bytes=computed(()=>new TextEncoder().encode(String(props.modelValue??'')).length)
const max=computed(()=>Math.min(props.schema.maxItems??1000,props.schema.maxUniqueItems??1000))
function setKey(k:string,v:any){emit('update:modelValue',{...(props.modelValue||{}),[k]:v})}
function setItem(i:number,v:any){const a=[...(props.modelValue||[])];a[i]=v;emit('update:modelValue',a)}
function blank(n:EditorSchema,example:any):any{if(n.type==='object')return Object.fromEntries(Object.entries(n.properties||{}).map(([k,v])=>[k,['marketplace_id','language_tag'].includes(k)?example?.[k]??v.default:blank(v,example?.[k])]));return n.type==='array'?[]:n.type==='string'?'':null}
function add(){emit('update:modelValue',[...(props.modelValue||[]),blank(props.schema.items!,props.modelValue?.[0])])}
</script>
<template>
 <div v-if="schema.type==='array'" class="editor-array"><div v-for="(item,i) in items" :key="i" class="editor-array-item"><div class="editor-array-head" v-if="(modelValue?.length||0)>1"><span>{{path==='bullet_point'?`第 ${i+1} 点`:`第 ${i+1} 项`}}</span><el-button v-if="path!=='bullet_point'&&(modelValue?.length||0)>1" link :disabled="disabled" @click="emit('update:modelValue',modelValue.filter((_:any,j:number)=>i!==j))">移除</el-button></div><ListingEditorNode :schema="schema.items!" :model-value="item" :baseline="baseline?.[i]" :path="`${path}.${i}`" :disabled="disabled" @update:model-value="v=>setItem(i,v)"/></div><el-button v-if="path!=='bullet_point'&&max>1&&(modelValue?.length||0)<max" link type="primary" :disabled="disabled" @click="add">＋ 添加一项</el-button></div>
 <div v-else-if="schema.type==='object'" class="editor-object" :class="{'single-value':fields.length===1}"><div v-for="([key,node]) in fields" :key="key" class="editor-property"><label v-if="fields.length>1||key!=='value'">{{node.title||key}}</label><ListingEditorNode :schema="node" :model-value="modelValue?.[key]" :baseline="baseline?.[key]" :path="`${path}.${key}`" :disabled="disabled||node.editable===false||node.readOnly" @update:model-value="v=>setKey(key,v)"/></div></div>
 <div v-else class="editor-control" :class="{modified}" :data-input-path="path">
 <span v-if="modified" class="control-modified">已修改</span>
 <el-select v-if="options.length||schema.type==='boolean'" :model-value="modelValue??undefined" :aria-label="path" filterable :allow-create="suggested" default-first-option placeholder="请选择，不自动填写" :disabled="disabled" @update:model-value="(v:any)=>emit('update:modelValue',v)"><el-option v-for="(v,i) in options.length?options:[true,false]" :key="String(v)" :label="enumNames[i]||((typeof v==='boolean')?(v?'是':'否'):String(v))" :value="v"/></el-select>
 <el-input-number v-else-if="schema.type==='number'||schema.type==='integer'" :aria-label="path" :model-value="modelValue??undefined" :precision="schema.type==='integer'?0:undefined" :disabled="disabled" placeholder="未填写" controls-position="right" @update:model-value="(v:any)=>emit('update:modelValue',v??null)"/>
 <el-input v-else :aria-label="path" :model-value="String(modelValue??'')" :disabled="disabled" :type="isLong?'textarea':'text'" :autosize="isLong?{minRows:path.startsWith('product_description')?6:2,maxRows:16}:undefined" placeholder="未填写" @update:model-value="(v:any)=>emit('update:modelValue',v)"/>
 <div v-if="schema.type==='string'&&!options.length" class="editor-count" :class="{over:(schema.maxLength!=null&&count>schema.maxLength)||(schema.maxUtf8ByteLength!=null&&bytes>schema.maxUtf8ByteLength)}">{{count}} {{path.startsWith('product_description')?'可见字符':'字符'}}<span v-if="schema.maxLength!=null"> / 模板上限 {{schema.maxLength}}</span><span v-if="schema.maxUtf8ByteLength!=null"> · {{bytes}} / {{schema.maxUtf8ByteLength}} 字节</span></div>
 </div>
</template>
<style scoped>
.editor-object{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:14px}.editor-object.single-value{display:block}.editor-property{min-width:0}.editor-property>label{display:block;font-size:11px;color:#7a8d80;margin-bottom:6px}.editor-array-item{margin-bottom:9px}.editor-array-head{display:flex;justify-content:space-between;align-items:center;font-size:11px;color:#7f9486;min-height:24px}.editor-count{font-size:10px;color:#90a095;text-align:right;margin-top:5px}.editor-count.over{color:#b95e40}.el-select,.el-input-number{width:100%}
.editor-control.modified :deep(.el-input__wrapper),.editor-control.modified :deep(.el-select__wrapper),.editor-control.modified :deep(.el-textarea__inner){box-shadow:0 0 0 1px #d5a13a inset;background:#fffaf0}.control-modified{display:block;color:#946919;font-size:10px;margin-bottom:4px}
</style>
