<script setup lang="ts">
import {ref,onMounted,onUnmounted,watch} from 'vue'
import {api,session} from './session'
import {useTableWidths} from './tableWidths'
const widths=useTableWidths('MailPanel'),store=ref(session.value?.current_store||''),q=ref(''),performanceOnly=ref(true),rows=ref<any[]>([]),total=ref(0),page=ref(1),pageSize=ref(25),busy=ref(false),error=ref(''),detail=ref<any>(null),opened=ref(false)
let request=0,detailRequest=0
onUnmounted(()=>{request++;detailRequest++})
async function load(){if(!store.value)return;const id=++request;busy.value=true;error.value='';try{const r=await api('/mail/messages?'+new URLSearchParams({store:store.value,q:q.value,performance_only:String(performanceOnly.value),page:String(page.value),page_size:String(pageSize.value)}));if(id===request){rows.value=r.rows;total.value=r.total}}catch(e){if(id===request)error.value=(e as Error).message}finally{if(id===request)busy.value=false}}
function search(){page.value=1;load()}
watch(store,()=>{detailRequest++;detail.value=null;opened.value=false;rows.value=[];search()})
async function show(row:any){const s=store.value,n=++detailRequest;try{const r=await api('/mail/message?'+new URLSearchParams({store:s,message_id:row.message_id}));if(s===store.value&&n===detailRequest){detail.value=r;opened.value=true}}catch(e){error.value=(e as Error).message}}
const time=(v:string)=>new Date(v).toLocaleString('zh-CN',{hour12:false})
onMounted(load)
</script>
<template><section>
 <p>先在「店铺授权管理」连接邮箱，再到「同步数据 → 绩效邮件」初始化或设置定时同步。仅归档亚马逊发件域名匹配的邮件；绩效分类是关键词初筛，不代表发件人已通过真实性验证。</p>
 <form class="mail-tools" @submit.prevent="search"><el-select v-model="store" aria-label="邮件店铺"><el-option v-for="s in session?.stores" :key="s" :value="s"/></el-select><el-input v-model="q" clearable placeholder="搜索邮件主题"/><el-checkbox v-model="performanceOnly" @change="search">仅绩效相关</el-checkbox><el-button native-type="submit" type="primary">查询</el-button><TableReset table-key="MailPanel/list"/></form>
 <el-alert v-if="error" :title="error" type="error" :closable="false"/>
 <el-table :data="rows" border v-loading="busy" data-width-key="MailPanel/list" @header-dragend="(w:number,_:number,c:any)=>widths.resize('list',w,c)">
  <el-table-column column-key="received" label="收件时间" :width="widths.width('list','received',180)"><template #default="{row}">{{time(row.received_at)}}</template></el-table-column>
  <el-table-column column-key="sender" prop="sender" label="发件人" :width="widths.width('list','sender',280)"/>
  <el-table-column column-key="subject" label="主题" min-width="320" :width="widths.width('list','subject')"><template #default="{row}"><el-button link type="primary" @click="show(row)">{{row.subject||'（无主题）'}}</el-button></template></el-table-column>
  <el-table-column column-key="category" label="分类" :width="widths.width('list','category',120)"><template #default="{row}">{{row.performance?'绩效相关':'其他亚马逊邮件'}}</template></el-table-column>
 </el-table><AppPagination v-model:current-page="page" v-model:page-size="pageSize" :total="total" @change="load"/>
 <el-drawer v-model="opened" title="邮件内容" size="min(850px,95vw)"><template v-if="detail"><h3>{{detail.subject}}</h3><p>{{detail.sender}} · {{time(detail.received_at)}}</p><p>纯文本归档，附件请在微软邮箱中查看。</p><pre class="mail-body">{{detail.body}}</pre></template></el-drawer>
</section></template>
<style scoped>.mail-tools{display:flex;gap:12px;flex-wrap:wrap;margin:18px 0}.mail-tools .el-select{width:190px}.mail-tools .el-input{width:260px}.mail-body{white-space:pre-wrap;overflow-wrap:anywhere;font:inherit;line-height:1.8}p{color:#71847a;line-height:1.7}</style>
