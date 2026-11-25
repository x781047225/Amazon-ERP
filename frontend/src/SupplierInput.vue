<script setup lang="ts">
import {ref} from 'vue'
import {api} from './session'
const props=defineProps<{modelValue:string,placeholder?:string}>()
const emit=defineEmits<{ 'update:modelValue':[value:string],change:[value:string] }>()
const error=ref('')
let sequence=0
async function suggestions(q:string,done:(rows:any[])=>void){const current=++sequence;error.value='';try{const result=await api('/product-data/suppliers?'+new URLSearchParams({q,page_size:'30'}));if(current===sequence)done(result.rows.map((s:any)=>({value:s.name})))}catch{if(current===sequence){done([]);error.value='供应商查询失败，可继续输入或稍后重试'}}}
</script>
<template><div class="supplier-input"><el-autocomplete :model-value="props.modelValue" :fetch-suggestions="suggestions" :debounce="250" :placeholder="placeholder||'输入供应商名称查询'" maxlength="500" clearable @update:model-value="emit('update:modelValue',String($event))" @change="emit('change',String($event))" @select="emit('change',String($event.value))"/><small v-if="error" role="status">{{error}}</small></div></template>
<style scoped>.supplier-input,.supplier-input :deep(.el-autocomplete){width:100%}small{color:#b16a30}</style>
