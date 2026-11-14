<script setup lang="ts">
import {ref,watch,onUnmounted} from 'vue'
import {ElMessage} from 'element-plus'
import {api} from './session'
const props=defineProps<{modelValue:boolean,taskId:string,name:string}>()
const emit=defineEmits(['update:modelValue','submitted'])
const remaining=ref(3),busy=ref(false),error=ref('')
let timer:ReturnType<typeof setInterval>|undefined
function clear(){if(timer)clearInterval(timer);timer=undefined}
watch(()=>props.modelValue,open=>{clear();if(open){remaining.value=3;error.value='';const until=Date.now()+3000;timer=setInterval(()=>{remaining.value=Math.max(0,Math.ceil((until-Date.now())/1000));if(!remaining.value)clear()},100)}},{immediate:true})
onUnmounted(clear)
async function submit(){if(remaining.value||busy.value)return;busy.value=true;error.value='';try{const task=await api(`/sta/${props.taskId}/action`,{action:'cancel'});emit('submitted',task);emit('update:modelValue',false);ElMessage.success('已提交删除，请等待亚马逊处理结果')}catch(e){error.value=(e as Error).message}finally{busy.value=false}}
</script>
<template><el-dialog :model-value="modelValue" @update:model-value="(v:boolean)=>!busy&&emit('update:modelValue',v)" title="删除货件和费用" width="560px" :close-on-click-modal="false" :show-close="!busy" :close-on-press-escape="!busy" class="shipment-delete-dialog"><p><b>{{name}}</b></p><el-alert title="删除货件后不可复原" type="error" :closable="false" show-icon/><p>将取消该 STA 入库计划下的全部货件，并申请撤销相关配送费用。亚马逊确认删除成功后，已出库库存按原批次重新入库；未出库的库存保持原状态。</p><p class="fee-notice">超出亚马逊免收费取消时限，仍可能产生费用，最终以亚马逊账单为准。</p><el-alert v-if="error" :title="error" type="error" :closable="false"/><template #footer><el-button :disabled="busy" @click="emit('update:modelValue',false)">暂不删除</el-button><el-button type="danger" :disabled="remaining>0" :loading="busy" @click="submit">{{remaining?`确定删除（${remaining} 秒）`:'确定删除'}}</el-button></template></el-dialog></template>
<style scoped>p{line-height:1.8}.fee-notice{color:#9b6329;font-size:12px}</style>
