<script setup lang="ts">
import {onMounted,onUnmounted,ref} from 'vue'
import {api} from './session'
const data=ref({unread:0,review:0,pending:0,overdue:0}),error=ref(false)
let timer:ReturnType<typeof setInterval>,loading=false,alive=true
async function refresh(){if(loading)return;loading=true;try{const result=await api('/todos/summary');if(alive){data.value=result;error.value=false;window.dispatchEvent(new CustomEvent('erp:todo-summary',{detail:result}))}}catch{if(alive)error.value=true}finally{loading=false}}
onMounted(()=>{refresh();timer=setInterval(refresh,30000);window.addEventListener('erp:todos-changed',refresh)})
onUnmounted(()=>{alive=false;clearInterval(timer);window.removeEventListener('erp:todos-changed',refresh)})
</script>
<template><span class="todo-nav-reminder" :class="{'needs-attention':data.unread||data.overdue}" :title="error?'提醒暂时无法更新':`待处理 ${data.pending} · 待验收 ${data.review} · 未读 ${data.unread} · 逾期 ${data.overdue}`" :aria-label="`待办 ${data.pending+data.review}，未读 ${data.unread}`">{{error?'!':data.pending+data.review}}<i v-if="data.unread" class="todo-unread-dot"/></span></template>
<style scoped>.todo-nav-reminder{position:relative;display:inline-flex;justify-content:center;align-items:center;min-width:23px;height:23px;padding:0 6px;border-radius:12px;background:#43566f;color:white;font-size:12px;font-weight:600;line-height:1;flex-shrink:0}.todo-nav-reminder.needs-attention{background:#c84e46}.todo-unread-dot{position:absolute;right:-2px;top:-2px;width:7px;height:7px;border-radius:50%;background:#ffcb70;border:1px solid #263548}</style>
