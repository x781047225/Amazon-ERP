<script setup lang="ts">
import {resetTableWidths} from './tableWidths'
defineProps<{tableKey:string}>()
</script>
<template><div class="list-table-tools" :data-reset-for="tableKey"><el-button text size="small" @click="resetTableWidths(tableKey)" title="恢复此列表的默认列宽">重置列宽</el-button></div></template>
<style scoped>.list-table-tools{display:inline-flex;flex-shrink:0;align-items:center;vertical-align:middle}.list-table-tools :deep(.el-button){color:#62768c;font-size:12px}</style>
