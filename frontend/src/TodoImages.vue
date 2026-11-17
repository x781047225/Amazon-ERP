<script setup lang="ts">
import {ref} from 'vue'
import {ElMessage} from 'element-plus'
import {api} from './session'
export type TodoPicture={id:string,url:string}
const props=defineProps<{modelValue:TodoPicture[],readonly?:boolean,disabled?:boolean}>()
const emit=defineEmits<{'update:modelValue':[TodoPicture[]],busy:[boolean]}>()
const input=ref<HTMLInputElement>(),uploading=ref(false)
async function uploadFiles(files:File[]){
 if(uploading.value||props.disabled||props.readonly)return
 if(files.length+props.modelValue.length>9){ElMessage.warning('每次最多上传9张图片');return}
 uploading.value=true;emit('busy',true)
 try{for(const file of files){
  if(file.size>8*1024*1024)throw new Error('每张图片不能超过8MB')
  const r=await fetch('/api/todos/images',{method:'POST',headers:{'Content-Type':file.type,'X-ERP-Client':'web'},body:file})
  const d=await r.json();if(!r.ok)throw new Error(typeof d.detail==='string'?d.detail:'图片上传失败')
  emit('update:modelValue',[...props.modelValue,d]);await new Promise(resolve=>setTimeout(resolve,0))
 }}catch(e){ElMessage.error((e as Error).message)}finally{uploading.value=false;emit('busy',false)}
}
function upload(event:Event){const files=Array.from((event.target as HTMLInputElement).files||[]);if(input.value)input.value.value='';uploadFiles(files)}
function paste(event:ClipboardEvent){const files=Array.from(event.clipboardData?.files||[]).filter(f=>f.type.startsWith('image/'));if(files.length){event.preventDefault();uploadFiles(files)}}
function drop(event:DragEvent){event.preventDefault();const files=Array.from(event.dataTransfer?.files||[]).filter(f=>f.type.startsWith('image/'));if(files.length)uploadFiles(files)}
defineExpose({paste,drop})
async function remove(image:TodoPicture){if(props.disabled)return;try{await api('/todos/images/'+image.id+'/remove',{});emit('update:modelValue',props.modelValue.filter(x=>x.id!==image.id))}catch(e){ElMessage.error((e as Error).message)}}
</script>
<template><div class="todo-images"><div v-for="(image,index) in modelValue" :key="image.id" class="todo-image"><el-image :src="image.url" :preview-src-list="modelValue.map(i=>i.url)" :initial-index="index" preview-teleported fit="contain"/><el-button v-if="!readonly" size="small" text type="danger" :disabled="uploading||disabled" :aria-label="'移除图片'+(index+1)" @click="remove(image)">移除</el-button></div><template v-if="!readonly"><input ref="input" type="file" accept="image/jpeg,image/png,image/webp" multiple hidden aria-label="上传待办图片" @change="upload"/><el-button :loading="uploading" :disabled="disabled||modelValue.length>=9" @click="input?.click()">添加图片（{{modelValue.length}}/9）</el-button><small>可粘贴截图或拖入图片 · 每张≤8MB</small></template></div></template>
<style scoped>.todo-images{display:flex;gap:12px;flex-wrap:wrap;align-items:center}.todo-image{display:flex;flex-direction:column;align-items:center}.todo-image .el-image{width:112px;height:100px;border:1px solid #dce5df;border-radius:8px;background:#f6f8f7}.todo-images small{color:#75877e;font-size:12px}</style>
