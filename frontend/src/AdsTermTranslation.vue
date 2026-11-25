<script setup lang="ts">
import {computed} from 'vue'
import {translatableTerm,termText,type TermTranslation} from './useAdsTranslations'
const props=defineProps<{level:string,row:any,state?:TermTranslation}>()
const eligible=computed(()=>translatableTerm(props.level,props.row))
const translated=computed(()=>String(props.state?.translation||props.row.chinese_translation||'').trim())
</script>
<template><div v-if="eligible" class="ads-term-translation" :title="state?.status==='unavailable'?'翻译服务暂不可用，稍后刷新列表重试':'Google 中文机器翻译，仅供理解原词'"><span v-if="translated&&translated!==termText(row)">{{translated}}</span><small v-else-if="!state||state.status==='loading'">翻译中…</small><small v-else-if="state.status==='unavailable'">翻译暂不可用</small></div></template>
<style scoped>.ads-term-translation{margin-top:3px;color:#7c9186;font-size:12px;line-height:1.6;white-space:normal;overflow-wrap:anywhere}.ads-term-translation:empty{display:none}.ads-term-translation small{font-size:11px;color:#99a59f}</style>
