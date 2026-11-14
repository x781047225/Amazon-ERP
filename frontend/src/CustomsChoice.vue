<script setup lang="ts">
import {can} from './session'
import {ref,onMounted,computed} from 'vue'
import {api,session} from './session'
import {vocabulary,loadVocabulary} from './customsData'
import {ElMessage} from 'element-plus'
const props=defineProps<{modelValue?:string,field:string,label:string}>(),emit=defineEmits(['update:modelValue']),values=computed(()=>vocabulary[props.field]||[]),draft=ref(''),busy=ref(false)
onMounted(async()=>{try{await loadVocabulary()}catch(e){ElMessage.error((e as Error).message)}})
async function add(){if(!draft.value.trim()||busy.value)return;busy.value=true;try{const d=await api('/customs/choices',{field:props.field,value:draft.value.trim()});if(!values.value.includes(d.value))vocabulary[props.field]=[...values.value,d.value];emit('update:modelValue',d.value);draft.value=''}catch(e){ElMessage.error((e as Error).message)}finally{busy.value=false}}
async function remove(value:string){if(busy.value)return;busy.value=true;try{await api('/customs/choices/delete',{field:props.field,value});vocabulary[props.field]=values.value.filter(v=>v!==value);if(props.modelValue===value)emit('update:modelValue','');ElMessage.success('选项已删除，已保存的产品资料保留')}catch(e){ElMessage.error((e as Error).message)}finally{busy.value=false}}
</script>
<template><el-select :model-value="modelValue" @update:model-value="emit('update:modelValue',$event)" filterable clearable :aria-label="label" placeholder="选择现有内容，或在下方添加"><el-option v-for="v in values" :key="v" :label="v" :value="v"><div class="choice-row"><span>{{v}}</span><el-button v-if="can('products.products','operate')" link type="danger" :disabled="busy" :aria-label="'删除'+label+'选项'+v" @mousedown.prevent.stop @click.prevent.stop="remove(v)">删除</el-button></div></el-option><template #footer><div class="add-choice" @click.stop><el-input v-model="draft" :aria-label="'添加'+label" placeholder="输入新内容" maxlength="500" @keydown.enter.prevent.stop="add"/><el-button type="primary" :loading="busy" :disabled="!draft.trim()" @click="add">添加</el-button></div></template></el-select></template>
<style scoped>.el-select{width:100%}.choice-row{display:flex;align-items:center;justify-content:space-between;gap:20px}.choice-row span{overflow:hidden;text-overflow:ellipsis}.choice-row .el-button{flex-shrink:0}.add-choice{display:flex;gap:8px;padding:4px}.add-choice .el-input{min-width:150px}</style>
