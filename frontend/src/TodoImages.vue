<script setup lang="ts">
import {ref,onMounted,onUnmounted,nextTick} from 'vue'
import {ElMessage} from 'element-plus'
import {api} from './session'
export type TodoPicture={id:string,url:string}
const props=defineProps<{modelValue:TodoPicture[],readonly?:boolean,disabled?:boolean}>()
const emit=defineEmits<{'update:modelValue':[TodoPicture[]],busy:[boolean]}>()
const input=ref<HTMLInputElement>(),uploading=ref(false),progress=ref(0),phase=ref(''),current=ref(0),total=ref(0)
let generation=0,xhr:XMLHttpRequest|undefined
function cancelUpload(notify=true){if(!uploading.value)return;generation++;xhr?.abort();xhr=undefined;uploading.value=false;emit('busy',false);if(notify)ElMessage.info('已取消剩余上传，已上传的图片保留')}
function escape(event:KeyboardEvent){if(event.key==='Escape'&&uploading.value){event.preventDefault();event.stopImmediatePropagation();cancelUpload()}}
onMounted(()=>window.addEventListener('keydown',escape,true))
onUnmounted(()=>{window.removeEventListener('keydown',escape,true);cancelUpload(false)})
async function prepare(file:File){
 // The server already limits stored images to 2400px; resize before transferring large screenshots.
 if(file.size<=512*1024||typeof createImageBitmap==='undefined')return file
 const bitmap=await createImageBitmap(file)
 try{
  if(bitmap.width*bitmap.height>25000000)throw new Error('图片不能超过2500万像素')
  const ratio=Math.min(1,2400/Math.max(bitmap.width,bitmap.height)),canvas=document.createElement('canvas')
  canvas.width=Math.max(1,Math.round(bitmap.width*ratio));canvas.height=Math.max(1,Math.round(bitmap.height*ratio))
  const ctx=canvas.getContext('2d');if(!ctx)return file
  ctx.fillStyle='#fff';ctx.fillRect(0,0,canvas.width,canvas.height);ctx.drawImage(bitmap,0,0,canvas.width,canvas.height)
  const blob=await new Promise<Blob|null>(resolve=>canvas.toBlob(resolve,'image/webp',.9))
  return blob&&blob.size<file.size?blob:file
 }finally{bitmap.close()}
}
function transfer(file:Blob,id:number):Promise<TodoPicture>{return new Promise((resolve,reject)=>{
 const request=new XMLHttpRequest();xhr=request;request.open('POST','/api/todos/images');request.timeout=90000
 request.setRequestHeader('Content-Type',file.type);request.setRequestHeader('X-ERP-Client','web')
 request.upload.onprogress=e=>{if(id!==generation)return;if(e.lengthComputable)progress.value=Math.round(e.loaded/e.total*100);phase.value=progress.value===100?'正在处理图片':'正在上传'}
 request.onabort=()=>reject(new DOMException('上传已取消','AbortError'))
 request.ontimeout=()=>reject(new Error('图片上传超时，请检查网络后重试'))
 request.onerror=()=>reject(new Error('图片上传连接中断，请检查网络后重试'))
 request.onload=()=>{
  if(request.status===401){window.dispatchEvent(new Event('erp:unauthorized'));reject(new Error('登录已过期，请重新登录'));return}
  if(request.status===413){reject(new Error('图片过大，上传被拒绝，请缩小图片后重试'));return}
  if(request.status>=500){reject(new Error('图片上传服务暂时不可用，请稍后重试'));return}
  let data:any
  try{data=JSON.parse(request.responseText)}catch{reject(new Error('图片上传未完成，服务返回异常，请稍后重试'));return}
  if(request.status<200||request.status>=300){reject(new Error(typeof data?.detail==='string'?data.detail:'图片上传失败，请重试'));return}
  if(typeof data?.id!=='string'||typeof data?.url!=='string'){reject(new Error('图片上传结果不完整，请重试'));return}
  resolve(data)
 }
 request.send(file)
})}
async function uploadFiles(files:File[]){
 if(!files.length||uploading.value||props.disabled||props.readonly)return
 if(files.length+props.modelValue.length>9){ElMessage.warning('每次最多上传9张图片');return}
 if(files.some(f=>f.size>8*1024*1024)){ElMessage.warning('每张图片不能超过8MB');return}
 if(files.some(f=>!['image/jpeg','image/png','image/webp'].includes(f.type))){ElMessage.warning('请选择 JPG、PNG 或 WebP 图片');return}
 const id=++generation;uploading.value=true;total.value=files.length;emit('busy',true)
 try{for(const [index,file] of files.entries()){
  current.value=index+1;progress.value=0;phase.value='正在准备图片'
  const prepared=await prepare(file);if(id!==generation)return
  phase.value='正在上传';const image=await transfer(prepared,id);if(id!==generation)return
  emit('update:modelValue',[...props.modelValue,image]);await nextTick()
 }}catch(e){if(id===generation)ElMessage.error((e as Error).message)}finally{if(id===generation){xhr=undefined;uploading.value=false;emit('busy',false)}}
}
function upload(event:Event){const files=Array.from((event.target as HTMLInputElement).files||[]);if(input.value)input.value.value='';uploadFiles(files)}
function paste(event:ClipboardEvent){const files=Array.from(event.clipboardData?.files||[]).filter(f=>f.type.startsWith('image/'));if(files.length){event.preventDefault();uploadFiles(files)}}
function drop(event:DragEvent){event.preventDefault();const files=Array.from(event.dataTransfer?.files||[]).filter(f=>f.type.startsWith('image/'));if(files.length)uploadFiles(files)}
defineExpose({paste,drop,cancelUpload})
async function remove(image:TodoPicture){if(props.disabled)return;try{await api('/todos/images/'+image.id+'/remove',{});emit('update:modelValue',props.modelValue.filter(x=>x.id!==image.id))}catch(e){ElMessage.error((e as Error).message)}}
</script>
<template><div class="todo-images"><div v-for="(image,index) in modelValue" :key="image.id" class="todo-image"><el-image :src="image.url" :preview-src-list="modelValue.map(i=>i.url)" :initial-index="index" preview-teleported fit="contain"/><el-button v-if="!readonly" size="small" text type="danger" :disabled="uploading||disabled" :aria-label="'移除图片'+(index+1)" @click="remove(image)">移除</el-button></div><template v-if="!readonly"><input ref="input" type="file" accept="image/jpeg,image/png,image/webp" multiple hidden aria-label="上传待办图片" @change="upload"/><el-button :loading="uploading" :disabled="disabled||modelValue.length>=9" @click="input?.click()">添加图片（{{modelValue.length}}/9）</el-button><div v-if="uploading" class="upload-status" role="status"><span>{{phase}} {{current}}/{{total}}<template v-if="phase==='正在上传'"> · {{progress}}%</template></span><el-button type="danger" plain size="small" @click="cancelUpload()">取消上传</el-button><small>按 Esc 也可取消</small></div><small v-else>可粘贴截图或拖入图片 · 每张≤8MB</small></template></div></template>
<style scoped>.todo-images{display:flex;gap:12px;flex-wrap:wrap;align-items:center}.todo-image{display:flex;flex-direction:column;align-items:center}.todo-image .el-image{width:112px;height:100px;border:1px solid #dce5df;border-radius:8px;background:#f6f8f7}.todo-images small{color:#75877e;font-size:12px}.upload-status{display:flex;align-items:center;gap:10px;flex-wrap:wrap;font-size:13px;color:#53675d}</style>
