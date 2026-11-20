<script setup lang="ts">
import {ref, reactive, onMounted, onUnmounted} from 'vue'
import {ElMessage} from 'element-plus'
import {api} from './session'

const rows=ref<any[]>([]), loading=ref(false), error=ref(''), dialog=ref(false), saving=ref(false)
const working=reactive<Record<string,boolean>>({}), exclusive=reactive<Record<string,boolean>>({})
const form=reactive<any>({}), configured=ref<Record<string,boolean>>({})
const regions=['eu-west-1','us-east-1','us-west-2','ap-northeast-1']
let timer:ReturnType<typeof setTimeout>|undefined, disposed=false
const date=(v:string|null)=>v?new Date(v).toLocaleString('zh-CN',{hour12:false}):'暂无'
const status=(row:any)=>({paused:'已暂停',running:'正在接收',waiting:'等待后台启动',error:'接收异常'}[row.consumer_state as string]||'待配置')
function replace(row:any){const i=rows.value.findIndex(r=>r.id===row.id);if(i<0)rows.value.push(row);else rows.value[i]=row}
async function load(quiet=false){
 if(!quiet)loading.value=true
 try{const r=await api('/settings/ads-queue');if(!disposed){rows.value=r.rows;error.value=''}}
 catch(e){if(!disposed)error.value=(e as Error).message}
 finally{loading.value=false}
}
async function poll(){await load(true);if(!disposed)timer=setTimeout(poll,10000)}
onMounted(async()=>{await load();if(!disposed)timer=setTimeout(poll,10000)})
onUnmounted(()=>{disposed=true;clearTimeout(timer)})
function edit(row?:any){
 Object.keys(form).forEach(k=>delete form[k])
 Object.assign(form,{id:row?.id,revision:row?.revision,name:row?.name||'',region:row?.region||'eu-west-1',queue_url:row?.queue_url||'',
  auth_mode:row?.auth_mode||'keys',profile_name:row?.profile_name||'',access_key_id:'',secret_access_key:'',session_token:'',clear_session_token:false})
 configured.value=row?.configured||{};dialog.value=true
}
async function save(){
 if(!form.name.trim()||!form.queue_url.trim()){ElMessage.warning('请填写队列名称和 Queue URL');return}
 saving.value=true
 try{replace(await api('/settings/ads-queue',{...form}));dialog.value=false;for(const k of ['access_key_id','secret_access_key','session_token'])form[k]='';ElMessage.success('已保存，请检查连接后开启消费')}
 catch(e){ElMessage.error((e as Error).message)}finally{saving.value=false}
}
async function check(row:any){
 working[row.id]=true
 try{const r=await api('/settings/ads-queue/'+row.id+'/test',{revision:row.revision});replace(r.row);r.ok?ElMessage.success(r.message):ElMessage.error(r.message)}
 catch(e){ElMessage.error((e as Error).message)}finally{working[row.id]=false}
}
async function toggle(row:any){
 working[row.id]=true
 try{replace(await api('/settings/ads-queue/'+row.id+'/consumption',{revision:row.revision,enabled:!row.enabled,exclusive_consumer:!!exclusive[row.id]}));ElMessage.success(row.enabled?'已暂停，正在处理的消息会完成保存':'已开启，后台将持续接收消息')}
 catch(e){ElMessage.error((e as Error).message)}finally{working[row.id]=false}
}
</script>

<template>
 <section class="ads-queue-panel" v-loading="loading">
  <div class="queue-toolbar"><h3>广告消息队列</h3><el-button @click="load()">刷新状态</el-button><el-button type="primary" @click="edit()">新增队列</el-button></div>
  <p class="queue-intro">填入已接通 Amazon Marketing Stream 的 SQS 队列。检查连接不会取走消息；开启后由 ERP 后台持续接收，关闭页面也会继续。</p>
  <el-alert title="当前接入用于保存广告消息，尚未合并到广告页面的统计中。" type="info" :closable="false"/>
  <el-alert v-if="error" :title="error" type="error" :closable="false"/>
  <el-empty v-if="!rows.length&&!loading&&!error" description="还没有配置广告消息队列"><el-button type="primary" @click="edit()">配置第一个队列</el-button></el-empty>
  <article v-for="row in rows" :key="row.id" class="queue-card">
   <div class="queue-title"><h4>{{row.name}}</h4><el-tag :type="row.consumer_state==='error'?'danger':row.consumer_state==='running'?'success':'info'">{{status(row)}}</el-tag><span class="muted">{{row.region}}</span></div>
   <div class="queue-url">{{row.queue_url}}</div>
   <div v-if="row.attributes?.arn" class="muted queue-url">ARN：{{row.attributes.arn}}</div>
   <div class="queue-metrics">
    <div><span>已保存消息</span><strong>{{row.received_count.toLocaleString()}}</strong></div>
    <div><span>已过滤重复</span><strong>{{row.duplicate_count.toLocaleString()}}</strong></div>
    <div><span>待识别消息</span><strong>{{row.quarantined_count.toLocaleString()}}</strong></div>
    <div><span>队列积压（约）</span><strong>{{row.attributes?.waiting??'—'}}</strong></div>
    <div><span>处理中（约）</span><strong>{{row.attributes?.inflight??'—'}}</strong></div>
   </div>
   <div class="queue-times"><span>最近接收：{{date(row.received_at)}}</span><span>后台心跳：{{date(row.heartbeat_at)}}</span><span>队列状态更新：{{date(row.attributes?.updated_at)}}</span></div>
   <div class="queue-datasets"><span>已接收数据类型：</span><el-tag v-for="(count,name) in row.datasets" :key="name" type="info">{{name}} · {{count}}</el-tag><span v-if="!Object.keys(row.datasets).length" class="muted">等待消息；连接成功不代表已订阅所有数据类型</span></div>
   <el-alert v-if="row.check_error||row.consumer_error" :title="row.consumer_error||row.check_error" type="error" :closable="false"/>
   <div class="queue-actions">
    <el-button :disabled="row.enabled||working[row.id]" @click="edit(row)">编辑配置</el-button>
    <el-button :loading="working[row.id]" @click="check(row)">检查连接</el-button>
    <el-tag v-if="row.connection_ok" type="success">连接已检查</el-tag>
    <el-checkbox v-if="!row.enabled" v-model="exclusive[row.id]">此队列没有其他程序同时消费</el-checkbox>
    <el-button :type="row.enabled?'default':'primary'" :disabled="working[row.id]||(!row.enabled&&(!row.connection_ok||!exclusive[row.id]))" @click="toggle(row)">{{row.enabled?'暂停消费':'开启消费'}}</el-button>
   </div>
  </article>
  <el-dialog v-model="dialog" class="queue-editor" :title="form.id?'编辑广告消息队列':'新增广告消息队列'" width="min(760px,95vw)" :close-on-click-modal="false" @closed="form.access_key_id='';form.secret_access_key='';form.session_token=''">
   <el-form label-position="top" :disabled="saving" @submit.prevent="save">
    <div class="queue-fields">
     <el-form-item label="队列名称" required><el-input v-model="form.name" placeholder="例如：欧洲广告消息" maxlength="80"/></el-form-item>
     <el-form-item label="AWS Region" required><el-select v-model="form.region" filterable allow-create default-first-option><el-option v-for="region in regions" :key="region" :label="region" :value="region"/></el-select></el-form-item>
    </div>
    <el-form-item label="Queue URL" required><el-input v-model="form.queue_url" placeholder="https://sqs.eu-west-1.amazonaws.com/账号ID/队列名称" maxlength="400"/></el-form-item>
    <el-form-item label="AWS 授权方式"><el-select v-model="form.auth_mode"><el-option label="Access Key / Secret Key" value="keys"/><el-option label="本机 AWS Profile" value="profile"/><el-option label="服务环境授权（IAM Role / 环境变量）" value="role"/></el-select></el-form-item>
    <template v-if="form.auth_mode==='keys'">
     <p class="muted">密钥加密保存在本地，不回显。编辑时留空保留原值；更换授权时同时填写 Access Key 和 Secret Key。</p>
     <div class="queue-fields"><el-form-item label="Access Key ID" :required="!configured.access_key_id"><el-input v-model="form.access_key_id" type="password" autocomplete="new-password" :placeholder="configured.access_key_id?'已配置；留空保留':''"/></el-form-item><el-form-item label="Secret Access Key" :required="!configured.secret_access_key"><el-input v-model="form.secret_access_key" type="password" autocomplete="new-password" :placeholder="configured.secret_access_key?'已配置；留空保留':''"/></el-form-item></div>
     <el-form-item label="Session Token（临时授权时填写）"><el-input v-model="form.session_token" type="password" autocomplete="new-password" :disabled="form.clear_session_token" :placeholder="configured.session_token?'已配置；留空保留':'长期 Access Key 不需要填写'"/></el-form-item>
     <el-checkbox v-if="configured.session_token" v-model="form.clear_session_token">清除旧的 Session Token</el-checkbox>
    </template>
    <el-form-item v-else-if="form.auth_mode==='profile'" label="本机 Profile 名称" required><el-input v-model="form.profile_name" placeholder="例如：erp-ads"/><span class="muted">使用运行 ERP 服务的电脑上已经配置好的 AWS Profile。</span></el-form-item>
    <p v-else class="muted">使用 ERP 服务进程可读取的 AWS 环境授权；不需要在此填写密钥。</p>
    <el-collapse class="queue-help"><el-collapse-item title="需要准备哪些 AWS 权限？" name="permissions">
     <p>只授予此队列的 ReceiveMessage、DeleteMessage、ChangeMessageVisibility、GetQueueAttributes 权限。如果队列使用自定义 KMS 密钥，还需要对应密钥的 Decrypt 权限。</p>
     <p>队列 ARN 在连接检查成功后显示，无需重复填写。请使用单独供 ERP 消费的队列；多个程序消费同一队列会分走消息。</p>
     <p>需要接收花费和点击、归因订单、预算使用情况时，请分别订阅 sp-traffic、sp-conversion、budget-usage。此处保存队列不会自动创建订阅。</p>
    </el-collapse-item></el-collapse>
   </el-form>
   <template #footer><el-button :disabled="saving" @click="dialog=false">取消</el-button><el-button type="primary" :loading="saving" @click="save">保存配置</el-button></template>
  </el-dialog>
 </section>
</template>

<style scoped>
.ads-queue-panel{margin-top:20px}.queue-toolbar,.queue-title,.queue-actions,.queue-times,.queue-datasets{display:flex;align-items:center;gap:12px;flex-wrap:wrap}.queue-toolbar h3{margin:0 auto 0 0}.queue-intro,.muted,.queue-times{color:var(--el-text-color-secondary);font-size:13px;line-height:1.7}.queue-card{border:1px solid var(--el-border-color-light);border-radius:8px;padding:20px;margin-top:16px}.queue-title h4{margin:0;font-size:16px}.queue-url{overflow-wrap:anywhere;margin-top:8px;font-size:13px}.queue-metrics{display:grid;grid-template-columns:repeat(5,minmax(100px,1fr));gap:16px;padding:20px 0}.queue-metrics span{display:block;font-size:13px;color:var(--el-text-color-secondary)}.queue-metrics strong{display:block;font-size:23px;line-height:1.8}.queue-datasets,.queue-actions{margin-top:16px}.queue-card .el-alert{margin-top:12px}.queue-fields{display:grid;grid-template-columns:1fr 1fr;gap:16px}.el-select{width:100%}.queue-help{margin-top:16px}.queue-help p{margin:8px 0}.ads-queue-panel>:deep(.el-alert){margin-top:12px}.queue-editor .el-checkbox{height:auto;white-space:normal}@media(max-width:700px){.queue-fields{grid-template-columns:1fr;gap:0}.queue-metrics{grid-template-columns:repeat(2,minmax(100px,1fr))}}
</style>
