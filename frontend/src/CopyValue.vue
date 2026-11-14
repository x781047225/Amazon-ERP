<script setup lang="ts">
import {DocumentCopy} from '@element-plus/icons-vue'
import {ElMessage} from 'element-plus'
const props=defineProps<{value?:string|null,label:string}>()
async function copy(){
 if(!props.value)return
 try{await navigator.clipboard.writeText(props.value);ElMessage.success({message:`${props.label} 已复制`,duration:1200,grouping:true})}
 catch{ElMessage.error('复制失败，请允许浏览器访问剪贴板后重试')}
}
</script>
<template><el-button v-if="value" class="copy-value" text size="small" :icon="DocumentCopy" :aria-label="`复制 ${label}`" :title="`复制 ${label}`" @click.stop="copy" @dblclick.stop/></template>
<style scoped>.copy-value{padding:3px!important;min-height:24px;height:24px;margin-left:2px!important;color:#69877a;flex-shrink:0}.copy-value:hover{color:#217660}</style>
