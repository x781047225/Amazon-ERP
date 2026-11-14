<script setup lang="ts">
import type {EditorSchema} from './listingTypes'
const props=defineProps<{value:any,schema?:EditorSchema|null}>()
function label(value:any){const i=props.schema?.enum?.findIndex(x=>x===value)??-1;return i>=0&&props.schema?.enumNames?.[i]?props.schema.enumNames[i]:typeof value==='boolean'?(value?'是':'否'):String(value)}
</script>
<template><span v-if="value==null||(Array.isArray(value)&&!value.length)" class="empty-value">未填写</span><div v-else-if="Array.isArray(value)" class="read-list"><div v-for="(v,i) in value" :key="i"><span v-if="value.length>1" class="read-index">{{i+1}}.</span><ListingEditorRead :value="v" :schema="schema?.items"/></div></div><div v-else-if="typeof value==='object'"><template v-for="(v,k) in value" :key="k"><div v-if="!['marketplace_id','language_tag'].includes(String(k))" class="read-property"><b v-if="k!=='value'">{{schema?.properties?.[k]?.title||k}}：</b><ListingEditorRead :value="v" :schema="schema?.properties?.[k]"/></div></template></div><span v-else class="read-text">{{label(value)}}</span></template>
<style scoped>.read-text{white-space:pre-wrap;overflow-wrap:anywhere}.read-property{margin-bottom:5px}.read-property b{font-size:11px;color:#72877a}.read-list>div{margin-bottom:12px}.read-index,.empty-value{color:#9aa89f}.read-index{display:block;font-size:10px}</style>
