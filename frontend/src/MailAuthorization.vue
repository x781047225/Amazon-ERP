<script setup lang="ts">
import {ref,watch,onUnmounted} from 'vue'
import {api,session} from './session'
import {ElMessage,ElMessageBox} from 'element-plus'
const props=defineProps<{store:string}>(),emit=defineEmits(['changed'])
const open=ref(false),email=ref(''),clientId=ref(''),savedClientId=ref(''),state=ref<any>({}),grant=ref<any>(null),error=ref(''),busy=ref(false)
let timer:ReturnType<typeof setTimeout>|undefined,generation=0,expires=0
function stop(){generation++;if(timer)clearTimeout(timer);timer=undefined;grant.value=null}
watch(open,v=>{if(!v)stop()});watch(()=>props.store,()=>{open.value=false;stop()});onUnmounted(stop)
async function show(){stop();open.value=true;error.value='';busy.value=true;const n=generation;try{const [app,conn]=await Promise.all([api('/mail/application'),api('/mail/connections/'+encodeURIComponent(props.store))]);if(n!==generation)return;clientId.value=savedClientId.value=app.client_id;state.value=conn;email.value=conn.email}catch(e){error.value=(e as Error).message}finally{busy.value=false}}
async function saveApplication(){busy.value=true;error.value='';try{await api('/mail/application',{client_id:clientId.value});savedClientId.value=clientId.value;ElMessage.success('微软应用已配置，所有店铺共用此应用')}catch(e){error.value=(e as Error).message}finally{busy.value=false}}
async function connect(){stop();error.value='';busy.value=true;const n=generation,store=props.store;try{const r=await api('/mail/connections/'+encodeURIComponent(store)+'/start',{email:email.value});if(n!==generation)return;grant.value=r;expires=Date.now()+r.expires_in*1000;timer=setTimeout(()=>poll(n,store),Math.max(5,r.interval||5)*1000)}catch(e){error.value=(e as Error).message}finally{busy.value=false}}
async function poll(n:number,store:string){if(n!==generation)return;if(Date.now()>expires){stop();error.value='授权已超时，请重新连接';return}try{const r=await api('/mail/connections/'+encodeURIComponent(store)+'/poll',{});if(n!==generation)return;if(r.status==='pending'){timer=setTimeout(()=>poll(n,store),5000);return}stop();if(r.status==='connected'){state.value=r;email.value=r.email;emit('changed');ElMessage.success('邮箱已绑定。请到同步数据中初始化并设置同步周期')}else error.value=r.message}catch(e){if(n===generation){stop();error.value=(e as Error).message}}}
async function disconnect(){try{await ElMessageBox.confirm('断开后停止该店铺的邮件同步，已归档邮件保留。','断开邮箱');busy.value=true;stop();await api('/mail/connections/'+encodeURIComponent(props.store)+'/disconnect',{});state.value.connected=false;emit('changed');ElMessage.success('已断开邮箱')}catch(e){if(e instanceof Error)error.value=e.message}finally{busy.value=false}}
</script>
<template>
 <el-button link type="primary" @click="show">授权邮箱</el-button>
 <el-dialog v-model="open" :title="store+' · 微软邮箱授权'" width="650px" append-to-body>
  <el-alert v-if="error" :title="error" type="error" :closable="false"/>
  <p>每个店铺独立连接邮箱。登录在微软官方页面完成，ERP 不保存邮箱密码。</p>
  <el-form label-position="top" :disabled="busy||!!grant">
   <el-form-item label="店铺邮箱"><el-input v-model="email" placeholder="填写完整的 Outlook / Hotmail / Microsoft 365 邮箱" autocomplete="email"/></el-form-item>
   <el-collapse v-if="session?.is_admin" :model-value="savedClientId?[]:['application']">
    <el-collapse-item name="application" title="首次接入：ERP 微软应用配置（全部店铺共用）">
     <p>在 Microsoft Entra 注册应用，支持个人与组织账户，启用公共客户端，配置委托权限 Mail.Read、User.Read 和 offline_access。无需 client_secret。详见部署文档“微软邮箱接入”。</p>
     <el-form-item label="应用（客户端）ID"><el-input v-model="clientId" placeholder="Application (client) ID"/></el-form-item>
     <el-button :disabled="!clientId" @click="saveApplication">保存应用配置</el-button>
    </el-collapse-item>
   </el-collapse>
   <p v-else-if="!savedClientId">请联系系统管理员先配置 ERP 微软应用。</p>
  </el-form>
  <p>授权状态：<el-tag :type="state.connected?'success':'info'">{{state.connected?'已连接 '+state.email:'未连接'}}</el-tag></p>
  <div v-if="grant" class="mail-grant">
   <p>打开微软授权页，输入本次由 ERP 生成的代码：</p><strong>{{grant.user_code}}</strong>
   <p><a :href="grant.verification_uri" target="_blank" rel="noopener noreferrer">打开微软官方授权页面</a></p>
   <p>请登录 {{email}} 并确认只读邮件授权。完成后此页面会自动更新。</p>
  </div>
  <template #footer><el-button @click="open=false">关闭</el-button><el-button v-if="state.connected" :disabled="busy" @click="disconnect">断开邮箱</el-button><el-button type="primary" :disabled="busy||!!grant||!email||!savedClientId" @click="connect">{{state.connected?'重新授权':'连接微软邮箱'}}</el-button></template>
 </el-dialog>
</template>
<style scoped>.mail-grant{background:#f1f7f5;padding:14px;border-radius:8px}.mail-grant strong{font-size:25px;letter-spacing:3px}.mail-grant a{color:#227660}p{line-height:1.7}</style>
