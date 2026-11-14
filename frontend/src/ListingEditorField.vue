<script setup lang="ts">
import {computed} from 'vue'
import ListingEditorNode from './ListingEditorNode.vue'
import ListingValue from './ListingValue.vue'
import type {EditField} from './listingTypes'
const props=defineProps<{field:EditField,modelValue:any,baseline:any,disabled?:boolean}>()
const modified=computed(()=>props.field.editable&&JSON.stringify(props.modelValue)!==JSON.stringify(props.baseline))
defineEmits<{'update:modelValue':[value:any]}>()
</script>
<template><div class="editor-field" :class="{modified}" :data-field="field.key"><div class="editor-field-head"><h3>{{field.label}} <span v-if="modified" class="modified-badge">已修改</span></h3><span>{{field.editable?'确认后提交亚马逊':'当前不可编辑'}}</span></div><ListingEditorNode v-if="field.editable&&field.schema" :schema="field.schema" :model-value="modelValue" :baseline="baseline" :path="field.key" :disabled="disabled" @update:model-value="v=>$emit('update:modelValue',v)"/><template v-else><p class="muted">{{field.reason}}</p><ListingValue v-if="field.raw?.length" :value="field.raw"/></template><p v-if="['item_dimensions','item_length_width_height','item_length_width','item_width_height','item_display_dimensions','item_weight'].includes(field.key)" class="editor-hint">产品本身的尺寸或净重，与下方运输包装尺寸、重量分别填写。<span v-if="field.key==='item_display_dimensions'">只填写适用的尺寸及单位，未填写的可选尺寸不会提交。</span></p><p v-if="field.key==='title_differentiation'" class="editor-hint">亮点独立于五点；旧链接没有此字段时可在这里补充。标题过长时亚马逊可能不展示亮点。</p><p v-if="field.key==='gpsr_safety_attestation'" class="editor-hint">此选项不是“已取得认证证书”。请按下方亚马逊说明选择，不自动填写。</p><details v-if="field.description" :open="field.section==='compliance'"><summary>亚马逊填写说明</summary><p>{{field.description}}</p></details></div></template>
<style scoped>
.editor-field{padding:18px 0;border-bottom:1px solid #edf1ee}.editor-field-head{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px}.editor-field-head h3{font-size:14px!important;margin:0!important;color:#395648}.editor-field-head>span{font-size:10px;color:#8e9e93}.editor-hint,details{font-size:11px;color:#809388;line-height:1.8}details{margin-top:8px}details p{white-space:pre-wrap}summary{cursor:pointer}
.editor-field.modified{border-left:3px solid #d6a23d;padding-left:14px}.modified-badge{display:inline-block;margin-left:8px;padding:2px 7px;border-radius:4px;background:#fff3d6;color:#8b6015;font-size:11px;font-weight:500}
</style>
