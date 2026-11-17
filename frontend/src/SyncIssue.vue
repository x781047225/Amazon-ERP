<script setup lang="ts">
import {computed,ref} from 'vue'
const props=defineProps<{message?:string|null}>(),open=ref(false)
const issues=computed(()=>{const counts=new Map<string,number>();for(const text of (props.message||'').split('；').map(s=>s.trim()).filter(Boolean))counts.set(text,(counts.get(text)||0)+1);return [...counts].map(([message,count])=>({message,count}))})
const summary=computed(()=>{const first=issues.value[0];return first?first.message.slice(0,140)+(first.message.length>140?'…':'')+(issues.value.length>1?`（共 ${issues.value.length} 类问题）`:first.count>1?`（重复 ${first.count} 次）`:''):''})
</script>
<template><div v-if="issues.length" class="sync-issue"><span class="issue-summary">{{summary}}</span><el-button link type="primary" @click="open=true">查看详情</el-button><el-dialog v-model="open" title="同步问题详情" width="min(680px, 90vw)" append-to-body><div class="issue-details"><p v-for="(item,i) in issues" :key="i">{{item.message}}<span v-if="item.count>1">（重复 {{item.count}} 次）</span></p></div><template #footer><el-button @click="open=false">关闭</el-button></template></el-dialog></div></template>
<style scoped>.sync-issue{font-size:13px;line-height:1.7;margin:8px 0}.issue-summary{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;color:#996b30;overflow-wrap:anywhere}.issue-details{max-height:50vh;overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere}</style>
