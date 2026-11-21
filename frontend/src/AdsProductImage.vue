<script setup lang="ts">
import {ref,watch} from 'vue'
import {api} from './session'
const props=defineProps<{profileId:string,asin:string}>()
const image=ref('');let request=0
watch(()=>[props.profileId,props.asin],async()=>{const r=++request;image.value='';if(!props.profileId||!props.asin)return;try{const data=await api('/advertising/search-actions/product-image?'+new URLSearchParams({profile_id:props.profileId,asin:props.asin}));if(r===request)image.value=data.image||''}catch{}},{immediate:true})
</script>
<template><el-image v-if="image" :src="image" fit="contain" style="width:46px;height:46px;flex-shrink:0" :alt="asin"/><span v-else class="image-empty">暂无图片</span></template>
<style scoped>.image-empty{width:46px;min-height:35px;display:inline-flex;align-items:center;color:#94a39c;font-size:11px;flex-shrink:0}</style>
