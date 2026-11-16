<script setup lang="ts">
import {nextTick} from 'vue'
const props=defineProps<{currentPage:number,pageSize:number,total:number}>()
const emit=defineEmits<{'update:currentPage':[number],'update:pageSize':[number],change:[]}>()
let pending=false,resetting=false
function changed(){
 if(pending)return
 pending=true
 nextTick(()=>{pending=false;resetting=false;emit('change')})
}
function setPage(value:number){
 if(resetting||value===props.currentPage)return
 emit('update:currentPage',value);changed()
}
function setSize(value:number){
 if(value===props.pageSize)return
 resetting=true
 emit('update:pageSize',value);emit('update:currentPage',1);changed()
}
</script>

<template>
 <div class="app-pagination">
  <el-pagination :current-page="currentPage" :page-size="pageSize" :total="total"
   :page-sizes="[25,50,100]" :pager-count="7" layout="total, sizes, prev, pager, next"
   @update:current-page="setPage" @update:page-size="setSize"/>
 </div>
</template>

<style scoped>
.app-pagination{display:flex;justify-content:flex-end;width:100%;min-width:0;padding:16px 0;background:#fff}
.app-pagination :deep(.el-pagination){justify-content:flex-end;flex-wrap:wrap;gap:8px;white-space:normal}
.app-pagination :deep(.el-pagination__total){margin-right:8px}
.app-pagination :deep(.el-pagination__sizes){margin-right:12px}
.app-pagination :deep(.el-select){width:128px}
.app-pagination :deep(.el-pager li){background:transparent}
@media(max-width:600px){.app-pagination :deep(.el-pagination){gap:4px}.app-pagination :deep(.el-pagination__sizes){margin-right:0}}
</style>
