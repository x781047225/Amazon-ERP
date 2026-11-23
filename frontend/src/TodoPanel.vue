<script setup lang="ts">
import {computed,onMounted,onUnmounted,reactive,ref,watch} from 'vue'
import {ElMessage,ElMessageBox} from 'element-plus'
import {api,can,session} from './session'
import TodoImages from './TodoImages.vue'
import type {TodoPicture} from './TodoImages.vue'
import {useTableWidths} from './tableWidths'
const props=withDefaults(defineProps<{active?:boolean}>(),{active:true})
const createUpload=ref<InstanceType<typeof TodoImages>>(),resultUpload=ref<InstanceType<typeof TodoImages>>()
const widths=useTableWidths('TodoPanel')
const labels:Record<string,string>={pending:'待处理',in_progress:'处理中',review:'待验收',returned:'已退回',completed:'已完成',cancelled:'已撤销'}
const colors:Record<string,'warning'|'primary'|'success'|'danger'|'info'>={pending:'warning',in_progress:'primary',review:'warning',returned:'danger',completed:'success',cancelled:'info'}
const actions:Record<string,string>={edit:'修改待办',create:'创建待办',start:'开始处理',submit:'提交验收',return:'退回处理',approve:'验收通过',comment:'留言',cancel:'撤销事项'}
const priorities:Record<number,string>={1:'低',2:'普通',3:'高',4:'紧急'}
const priorityColors:Record<number,'info'|'primary'|'warning'|'danger'>={1:'info',2:'primary',3:'warning',4:'danger'}
const sort=ref('priority_desc'),storeFilter=ref('')
const contextOptions=ref<{stores:string[],marketplaces:string[]}>({stores:[],marketplaces:[]})
const marketNames:Record<string,string>={UK:'英国',DE:'德国',FR:'法国',IT:'意大利',ES:'西班牙',NL:'荷兰',BE:'比利时',PL:'波兰',SE:'瑞典',IE:'爱尔兰',AE:'阿联酋',SA:'沙特'}
const marketLabel=(m:string)=>m?`${marketNames[m]||m} · ${m}`:'—'
const view=ref('assigned'),status=ref(''),query=ref(''),page=ref(1),pageSize=ref(25),total=ref(0),rows=ref<any[]>([]),busy=ref(false),error=ref('')
const summary=ref<any>({unread:0,review:0,pending:0,overdue:0,initiated_unread:0,assigned_unread:0})
const editing=ref<any>(null)
const creating=ref(false),saving=ref(false),uploading=ref(false),users=ref<any[]>([]),formError=ref('')
const form=reactive({store:'',marketplace:'',title:'',description:'',instructions:'',assignee_id:null as number|null,deadline:'' as any,priority:2,cc_user_ids:[] as number[]})
const createImages=ref<TodoPicture[]>([]),resultImages=ref<TodoPicture[]>([]),note=ref(''),detail=ref<any>(null),detailOpen=ref(false),detailBusy=ref(false),actionError=ref('')
const assignee=computed(()=>detail.value?.assignee_id===session.value?.id),creator=computed(()=>detail.value?.creator_id===session.value?.id)
const canProcess=computed(()=>can('todos','operate')&&assignee.value&&['pending','in_progress','returned'].includes(detail.value?.status))
const canReview=computed(()=>can('todos','operate')&&creator.value&&detail.value?.status==='review')
const ccOptions=computed(()=>users.value.filter(u=>u.id!==session.value?.id&&u.id!==form.assignee_id))
watch(()=>form.assignee_id,()=>{form.cc_user_ids=form.cc_user_ids.filter(id=>id!==form.assignee_id)})
const canCancel=(row:any)=>can('todos','operate')&&row.creator_id===session.value?.id&&!['completed','cancelled'].includes(row.status)
const format=(value:string)=>new Date(value).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false})
let seq=0,alive=true
function changed(){window.dispatchEvent(new Event('erp:todos-changed'))}
async function load(background=false){
 if(!props.active)return
 const id=++seq;if(!background)busy.value=true
 try{const d=await api('/todos?'+new URLSearchParams({...(storeFilter.value?{store:storeFilter.value}:{}),view:view.value,status:status.value,sort:sort.value,q:query.value,page:String(page.value),page_size:String(pageSize.value)}));if(alive&&id===seq){if(JSON.stringify(rows.value)!==JSON.stringify(d.items))rows.value=d.items;total.value=d.total;error.value=''}}catch(e){if(alive&&id===seq)error.value=(e as Error).message}finally{if(id===seq)busy.value=false}
}
function search(){page.value=1;load()}
function receive(event:Event){summary.value=(event as CustomEvent).detail;if(props.active&&document.visibilityState==='visible'){load(true);if(detailOpen.value&&detail.value&&!saving.value&&!uploading.value)refreshDetail(detail.value.id,true)}}
async function newTodo(){editing.value=null;formError.value='';Object.assign(form,{store:'',marketplace:'',title:'',description:'',instructions:'',assignee_id:null,deadline:'',priority:2,cc_user_ids:[]});createImages.value=[];creating.value=true;try{users.value=await api('/todos/users')}catch(e){formError.value=(e as Error).message}}
async function editTodo(row:any){
 try{const [d,u]=await Promise.all([api('/todos/'+row.id),api('/todos/users')]);editing.value=d;users.value=u;Object.assign(form,{store:d.store||'',marketplace:d.marketplace||'',title:d.title,description:d.description,instructions:d.instructions,assignee_id:d.assignee_id,deadline:new Date(d.deadline),priority:d.priority,cc_user_ids:d.cc_users.map((c:any)=>c.id)});createImages.value=[];formError.value='';creating.value=true}catch(e){ElMessage.error((e as Error).message)}
}
async function abandon(images:TodoPicture[]){await Promise.allSettled(images.map(i=>api('/todos/images/'+i.id+'/remove',{})))}
async function closeCreate(done?:()=>void){if(saving.value||uploading.value)return;if(form.title||form.description||form.instructions||createImages.value.length){try{await ElMessageBox.confirm('关闭后未保存的内容将丢失，是否关闭？',editing.value?'放弃修改':'放弃新建',{type:'warning'})}catch{return}}abandon(createImages.value);createImages.value=[];creating.value=false;done?.()}
async function create(){
 formError.value=''
 if(!form.title.trim()||!form.description.trim()||!form.instructions.trim()||!form.assignee_id||!form.deadline){formError.value='请填写标题、事件详情、处理要求、负责人和截止时间';return}
 if(new Date(form.deadline).getTime()<=Date.now()&&(!editing.value||new Date(form.deadline).getTime()!==new Date(editing.value.deadline).getTime())){formError.value='截止时间应晚于当前时间';return}
 saving.value=true
 try{const d=await api(editing.value?'/todos/'+editing.value.id+'/edit':'/todos',{...form,...(editing.value?{version:editing.value.version}:{}),deadline:new Date(form.deadline).toISOString(),image_ids:createImages.value.map(i=>i.id)});createImages.value=[];creating.value=false;view.value='initiated';status.value='';query.value='';storeFilter.value='';page.value=1;await load();changed();ElMessage.success(editing.value?(d.unchanged?'内容没有变化':d.notified?'修改已保存，已提醒处理人':'修改已保存'):'待办已分配，负责人会收到提醒');await openTask(d.id)}catch(e){formError.value=(e as Error).message}finally{saving.value=false}
}
let detailRequest=0
async function refreshDetail(id:number,background=false){
 const request=++detailRequest
 try{const d=await api('/todos/'+id)
  if(!alive||!detailOpen.value||request!==detailRequest||background&&saving.value)return
  if(d.unread&&d.assignee_id===session.value?.id&&d.events?.at(-1)?.action==='edit'&&(!detail.value||d.version>detail.value.version))ElMessage.warning('发起人修改了此待办，请查看修改记录后继续处理')
  if(!detail.value||d.version>=detail.value.version){if(JSON.stringify(detail.value)!==JSON.stringify(d))detail.value=d}
  if(d.unread&&props.active&&document.visibilityState==='visible'){await api('/todos/'+id+'/seen',{version:d.version});if(detail.value?.id===id)detail.value.unread=false;changed()}
 }catch(e){if(request===detailRequest)actionError.value=(e as Error).message}
}
async function openTask(id:number){detailOpen.value=true;detailBusy.value=true;detail.value=null;note.value='';resultImages.value=[];actionError.value='';try{await refreshDetail(id);load(true)}finally{detailBusy.value=false}}

async function closeDetail(done?:()=>void){if(saving.value||uploading.value)return;if(note.value.trim()||resultImages.value.length){try{await ElMessageBox.confirm('说明和图片尚未提交，是否关闭？','放弃本次填写',{type:'warning'})}catch{return}}abandon(resultImages.value);resultImages.value=[];note.value='';detailOpen.value=false;done?.()}
async function act(action:string){
 if(!detail.value||saving.value||uploading.value)return;actionError.value='';detailRequest++
 if(action==='comment'&&!note.value.trim()&&!resultImages.value.length){actionError.value='请输入消息或添加图片';return}
 if(['submit','return'].includes(action)&&!note.value.trim()){actionError.value=action==='submit'?'请填写处理过程和验证结果':'请说明退回原因和需要补充的内容';return}
 if(action==='approve'){try{await ElMessageBox.confirm('确认验收通过并标记为已完成？','验收确认',{type:'success'})}catch{return}}
 saving.value=true
 try{await api('/todos/'+detail.value.id+'/actions',{version:detail.value.version,action,note:action==='start'?'':note.value,image_ids:action==='start'?[]:resultImages.value.map(i=>i.id)});if(action!=='start'){note.value='';resultImages.value=[]}const id=detail.value.id;await refreshDetail(id);changed();await load();ElMessage.success(actions[action]||'状态已更新')}catch(e){actionError.value=(e as Error).message}finally{saving.value=false}
}
async function refreshPage(){await load();changed();try{summary.value=await api('/todos/summary')}catch(e){error.value=(e as Error).message}}
async function cancelTask(row:any){
 if(saving.value||uploading.value)return
 let reason=''
 try{const answer=await ElMessageBox.prompt('撤销后停止处理与验收，历史记录仍保留。请填写撤销原因。','撤销事项',{confirmButtonText:'确认撤销',cancelButtonText:'取消',inputValidator:(v:string)=>!!v?.trim()&&v.length<=10000||'请填写撤销原因（最多10000字）',type:'warning'});reason=answer.value.trim()}catch{return}
 saving.value=true
 try{await api('/todos/'+row.id+'/actions',{version:row.version,action:'cancel',note:reason});if(detailOpen.value&&detail.value?.id===row.id)await refreshDetail(row.id);await refreshPage();ElMessage.success('事项已撤销，历史记录已保留')}catch(e){ElMessage.error((e as Error).message);await load(true)}finally{saving.value=false}
}
onMounted(()=>{load();api('/todos/options').then(d=>{if(alive)contextOptions.value=d}).catch(e=>{error.value=(e as Error).message});api('/todos/summary').then(d=>{if(alive)summary.value=d}).catch(()=>{});window.addEventListener('erp:todo-summary',receive)})
watch(()=>props.active,active=>{if(active){load(true);if(detailOpen.value&&detail.value)refreshDetail(detail.value.id,true)}else{seq++;busy.value=false}})
onUnmounted(()=>{alive=false;seq++;detailRequest++;window.removeEventListener('erp:todo-summary',receive)})
</script>
<template><section id="personal-todos" class="panel todo-panel">
 <div class="panel-head"><div><h2>待办事项</h2><p class="metrics-note">明确负责人，提交结果，由发起人验收完成。</p></div></div>
 <div class="todo-summary" aria-live="polite"><span>待我处理 <b>{{summary.pending}}</b></span><span>待我验收 <b>{{summary.review}}</b></span><span :class="{'todo-danger':summary.overdue}">已逾期 <b>{{summary.overdue}}</b></span><span v-if="summary.unread" class="todo-danger">{{summary.unread}} 项新动态</span><small>状态提醒每30秒更新</small></div>
 <el-tabs v-model="view" @tab-change="status='';search()"><el-tab-pane name="initiated"><template #label>我发起的 <el-tag v-if="summary.initiated_unread" size="small" type="danger">{{summary.initiated_unread}}</el-tag></template></el-tab-pane><el-tab-pane name="assigned"><template #label>我负责的 <el-tag v-if="summary.assigned_unread" size="small" type="danger">{{summary.assigned_unread}}</el-tag></template></el-tab-pane><el-tab-pane name="copied"><template #label>抄送我的 <el-tag v-if="summary.copied_unread" size="small" type="danger">{{summary.copied_unread}}</el-tag></template></el-tab-pane><el-tab-pane name="completed" label="已完成"/></el-tabs>
 <div class="todo-filters"><el-input v-model="query" aria-label="搜索待办标题" placeholder="搜索待办标题" clearable maxlength="200" @keyup.enter="search"/><el-select v-model="storeFilter" aria-label="按店铺筛选待办" placeholder="全部店铺" clearable filterable :value-on-clear="''" @change="search"><el-option v-for="s in contextOptions.stores" :key="s" :value="s" :label="s"/></el-select><el-select v-if="view!=='completed'" v-model="status" aria-label="待办状态" placeholder="全部状态" clearable @change="search"><el-option v-for="(text,key) in labels" :key="key" :value="key" :label="text"/><el-option value="overdue" label="已逾期"/></el-select><el-select v-model="sort" aria-label="待办排序" @change="search"><el-option value="priority_desc" label="优先级降序"/><el-option value="deadline_desc" label="截止时间降序"/><el-option value="updated_desc" label="最近动态降序"/></el-select><el-button type="primary" @click="search">查询</el-button><el-button @click="query='';status='';storeFilter='';sort='priority_desc';search()">重置</el-button><el-button v-if="can('todos','operate')" type="primary" @click="newTodo">新建待办</el-button><el-button :loading="busy" @click="refreshPage">刷新页面</el-button><TableReset table-key="TodoPanel/tasks"/></div>
 <el-alert v-if="error" :title="error" type="error" :closable="false"/>
 <el-table v-loading="busy" :data="rows" border data-width-key="TodoPanel/tasks" @header-dragend="(w:number,_old:number,c:any)=>widths.resize('tasks',w,c)" @header-contextmenu="(c:any,e:MouseEvent)=>widths.resetColumn('tasks',c,e)">
  <el-table-column label="事项" column-key="title" :width="widths.width('tasks','title',undefined)" min-width="230"><template #default="{row}"><el-button text type="primary" class="todo-title" @click="openTask(row.id)">{{row.title}}</el-button><el-tag v-if="row.unread" type="danger" size="small">新动态</el-tag></template></el-table-column>
  <el-table-column label="店铺 / 站点" column-key="store-marketplace" :width="widths.width('tasks','store-marketplace',180)"><template #default="{row}"><div>{{row.store||'—'}}</div><div v-if="row.marketplace" class="metrics-note">{{marketLabel(row.marketplace)}}</div></template></el-table-column>
  <el-table-column label="优先级" column-key="priority" :width="widths.width('tasks','priority',100)"><template #default="{row}"><el-tag :type="priorityColors[row.priority]">{{priorities[row.priority]}}</el-tag></template></el-table-column>
  <el-table-column label="状态" column-key="status" :width="widths.width('tasks','status',150)"><template #default="{row}"><el-tag :type="colors[row.status]">{{labels[row.status]}}</el-tag> <el-tag v-if="row.overdue" type="danger" size="small">逾期</el-tag></template></el-table-column>
  <el-table-column prop="creator" column-key="creator" label="发起人" :width="widths.width('tasks','creator',120)"/><el-table-column prop="assignee" column-key="assignee" label="负责人" :width="widths.width('tasks','assignee',120)"/>
  <el-table-column label="截止时间（北京时间）" column-key="deadline" :width="widths.width('tasks','deadline',210)"><template #default="{row}"><span :class="{'todo-danger':row.overdue}">{{format(row.deadline)}}</span></template></el-table-column>
  <el-table-column label="最近动态" column-key="updated" :width="widths.width('tasks','updated',200)"><template #default="{row}">{{format(row.updated_at)}}</template></el-table-column>
  <el-table-column label="操作" width="280"><template #default="{row}"><div class="todo-row-actions"><el-button text type="primary" @click="openTask(row.id)">{{row.status==='review'&&row.creator_id===session?.id?'查看验收':'查看详情'}}</el-button><el-button v-if="canCancel(row)" text type="primary" :disabled="saving||uploading" @click="editTodo(row)">编辑</el-button><el-button v-if="canCancel(row)" text type="danger" :disabled="saving||uploading" @click="cancelTask(row)">撤销</el-button></div></template></el-table-column>
  <template #empty><el-empty description="暂无待办事项" :image-size="60"/></template>
 </el-table><AppPagination v-model:current-page="page" v-model:page-size="pageSize" :total="total" @change="load()"/>
 <el-dialog v-model="creating" :title="editing?'编辑待办':'新建待办'" width="min(760px, 94vw)" :close-on-click-modal="false" :before-close="closeCreate" destroy-on-close>
  <el-alert v-if="editing&&editing.status!=='pending'" title="保存后会提醒处理人查看修改，处理状态保持不变。" type="info" :closable="false"/><el-alert v-if="formError" :title="formError" type="error" :closable="false"/>
  <el-form label-position="top"><div class="todo-form-pair"><el-form-item label="店铺（选填）"><el-select v-model="form.store" aria-label="待办店铺（选填）" placeholder="请选择店铺，可留空" clearable filterable :value-on-clear="''"><el-option v-for="s in contextOptions.stores" :key="s" :value="s" :label="s"/></el-select></el-form-item><el-form-item label="站点（选填）"><el-select v-model="form.marketplace" aria-label="待办站点（选填）" placeholder="请选择站点，可留空" clearable :value-on-clear="''"><el-option v-for="m in contextOptions.marketplaces" :key="m" :value="m" :label="marketLabel(m)"/></el-select></el-form-item></div><el-form-item label="事项标题" required><el-input v-model="form.title" maxlength="200" show-word-limit/></el-form-item><div class="todo-form-pair"><el-form-item label="负责人" required><el-select v-model="form.assignee_id" filterable placeholder="选择负责人"><el-option v-for="u in users" :key="u.id" :value="u.id" :label="u.username"/></el-select></el-form-item><el-form-item label="截止时间（按当前设备时区填写）" required><el-date-picker v-model="form.deadline" type="datetime" placeholder="选择截止日期和时间" style="width:100%"/></el-form-item></div><div class="todo-form-pair"><el-form-item label="优先级" required><el-select v-model="form.priority" aria-label="事项优先级"><el-option v-for="(text,key) in priorities" :key="key" :value="Number(key)" :label="text"/></el-select></el-form-item><el-form-item label="抄送人"><el-select v-model="form.cc_user_ids" aria-label="抄送人" multiple filterable clearable placeholder="可选择多人，允许查看和留言"><el-option v-for="u in ccOptions" :key="u.id" :value="u.id" :label="u.username"/></el-select></el-form-item></div><el-form-item label="事件详情" required><div class="todo-composer" @paste="createUpload?.paste($event)" @dragover.prevent @drop="createUpload?.drop($event)"><el-input v-model="form.description" type="textarea" :rows="4" maxlength="10000" show-word-limit placeholder="描述背景、问题和涉及的事项，可直接粘贴截图"/><TodoImages ref="createUpload" v-model="createImages" :disabled="saving" @busy="uploading=$event"/></div></el-form-item><el-form-item label="处理要求 / 验收标准" required><el-input v-model="form.instructions" type="textarea" :rows="3" maxlength="10000" show-word-limit placeholder="说明怎么处理，以及达到什么结果可以验收"/></el-form-item></el-form>
  <template #footer><el-button :disabled="saving||uploading" @click="closeCreate()">取消</el-button><el-button type="primary" :loading="saving" :disabled="uploading" @click="create">{{editing?'保存修改':'创建并分配'}}</el-button></template>
 </el-dialog>
 <el-drawer v-model="detailOpen" title="待办详情" size="min(820px, 96vw)" :before-close="closeDetail" :close-on-click-modal="false" destroy-on-close><div v-loading="detailBusy">
  <el-alert v-if="actionError" :title="actionError" type="error" :closable="false"/>
  <template v-if="detail"><h2 class="todo-detail-title">{{detail.title}}</h2><div class="todo-detail-meta"><span v-if="detail.store||detail.marketplace">{{detail.store||'未指定店铺'}} / {{marketLabel(detail.marketplace)}}</span><el-tag :type="priorityColors[detail.priority]">{{priorities[detail.priority]}}优先级</el-tag><el-tag :type="colors[detail.status]">{{labels[detail.status]}}</el-tag><el-tag v-if="detail.overdue" type="danger">已逾期</el-tag><span>{{detail.creator}} 发起 → {{detail.assignee}} 负责</span><span>截止 {{format(detail.deadline)}}（北京时间）</span><span v-if="detail.cc_users?.length">抄送：{{detail.cc_users.map((u:any)=>u.username).join('、')}}（可查看和留言）</span></div>
   <div class="todo-conversation" aria-label="事项对话记录">
    <article v-for="event in detail.events" :key="event.id" class="todo-message" :class="{mine:event.actor_id===session?.id}">
     <div class="todo-message-avatar">{{event.actor.slice(0,1).toUpperCase()}}</div>
     <div class="todo-message-content"><div class="todo-message-meta"><b>{{event.actor}}</b><time>{{format(event.created_at)}}</time><el-tag size="small" :type="colors[event.status]">{{actions[event.action]}}</el-tag></div>
      <div class="todo-bubble"><template v-if="event.action==='create'"><b>事件详情</b><p class="todo-prose">{{detail.initial_description??detail.description}}</p><b>处理要求 / 验收标准</b><p class="todo-prose">{{detail.initial_instructions??detail.instructions}}</p></template><p v-else-if="event.note" class="todo-prose">{{event.note}}</p><span v-else class="metrics-note">{{actions[event.action]}}</span><TodoImages v-if="event.images.length" :model-value="event.images" readonly/></div>
     </div>
    </article>
   </div>
   <section v-if="can('todos','operate')" class="todo-action"><h3>{{canReview?'回复与验收':canProcess?'回复与提交结果':'补充留言'}}</h3><p class="metrics-note">文字与图片一起发送，保留在对话记录中。{{canReview?'验收通过后标记完成；退回时请说明原因。':canProcess?'提交验收时请说明处理过程与验证结果。':'补充留言不会改变事项状态。'}}</p>
    <div class="todo-composer" @paste="resultUpload?.paste($event)" @dragover.prevent @drop="resultUpload?.drop($event)"><el-input v-model="note" aria-label="处理与验收说明" type="textarea" :rows="4" maxlength="10000" show-word-limit placeholder="输入消息、处理结果或验收意见，可直接粘贴截图"/><TodoImages ref="resultUpload" v-model="resultImages" :disabled="saving" @busy="uploading=$event"/></div>
    <div class="todo-action-buttons"><el-button v-if="canCancel(detail)" type="danger" plain :disabled="saving||uploading" @click="cancelTask(detail)">撤销事项</el-button><el-button :disabled="saving||uploading" @click="act('comment')">发送留言</el-button><template v-if="canProcess"><el-button v-if="detail.status!=='in_progress'" :disabled="saving||uploading" @click="act('start')">开始处理</el-button><el-button type="primary" :disabled="uploading" :loading="saving" @click="act('submit')">提交验收</el-button></template><template v-if="canReview"><el-button type="danger" plain :disabled="saving||uploading" @click="act('return')">退回处理</el-button><el-button type="success" :disabled="uploading" :loading="saving" @click="act('approve')">验收通过</el-button></template></div>
   </section>
  </template></div></el-drawer>
</section></template>
<style scoped>
.todo-row-actions{display:flex;align-items:center;gap:8px;flex-wrap:nowrap;white-space:nowrap}.todo-row-actions .el-button{flex-shrink:0;margin:0}

.todo-panel>.panel-head>div{flex:1;min-width:180px}.todo-panel>.panel-head h2{white-space:nowrap}.todo-panel{margin-top:0;scroll-margin-top:24px}.todo-summary{display:flex;gap:22px;flex-wrap:wrap;padding:16px;background:#f1f7f5;border-radius:8px;margin-bottom:16px}.todo-summary b{font-size:20px;margin-left:6px}.todo-summary small{margin-left:auto;color:#71867c;align-self:center}.todo-danger{color:#ce3d45}.todo-panel :deep(.el-tabs__nav-wrap){padding-left:16px;padding-right:16px}.todo-filters{padding:0 16px;display:flex;gap:10px;flex-wrap:wrap;margin:0 0 16px}.todo-filters .el-input{width:280px}.todo-filters .el-select{width:160px}.todo-title{white-space:normal;text-align:left;height:auto;max-width:100%;overflow-wrap:anywhere}.todo-form-pair{display:grid;grid-template-columns:1fr 1fr;gap:20px}.todo-form-pair .el-select{width:100%}.todo-detail-title{overflow-wrap:anywhere}.todo-detail-meta{display:flex;flex-wrap:wrap;gap:10px;color:#63786e;margin-bottom:24px}.todo-prose{white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.7}.todo-action{padding:20px;background:#f3f8f6;border-radius:10px}.todo-action .todo-images{margin-top:20px}.todo-action-buttons{display:flex;gap:10px;flex-wrap:wrap;justify-content:flex-end;margin-top:20px}.todo-panel :deep(.el-form-item__label){font-weight:600}@media(max-width:600px){.todo-form-pair{grid-template-columns:1fr;gap:0}.todo-summary{gap:12px}.todo-filters .el-input{width:100%}}
.todo-composer{border:1px solid #cbdad3;border-radius:10px;background:#fff;padding:12px;width:100%}.todo-composer :deep(.el-textarea__inner){box-shadow:none;resize:vertical}.todo-composer .todo-images{margin-top:12px;border-top:1px solid #e8eeeb;padding-top:12px}.todo-conversation{display:flex;flex-direction:column;gap:24px;margin:24px 0}.todo-message{display:flex;gap:12px;align-items:flex-start}.todo-message-avatar{width:34px;height:34px;flex-shrink:0;border-radius:50%;background:#dfe8ef;color:#405a73;display:grid;place-items:center;font-weight:700}.todo-message-content{max-width:calc(100% - 46px);min-width:0}.todo-message-meta{display:flex;align-items:center;gap:10px;flex-wrap:wrap;font-size:12px;color:#6b7c73;margin-bottom:8px}.todo-message-meta time{font-size:11px}.todo-bubble{padding:16px;border:1px solid #e0e9e4;border-radius:0 12px 12px;background:#f7faf8;overflow-wrap:anywhere}.todo-bubble .todo-prose:first-child{margin-top:0}.todo-bubble .todo-prose:last-child{margin-bottom:0}.todo-message.mine{flex-direction:row-reverse}.mine .todo-message-avatar{background:#d1eae0;color:#227660}.mine .todo-message-meta{justify-content:flex-end}.mine .todo-bubble{background:#edf7f2;border-radius:12px 0 12px 12px}.todo-conversation :deep(.todo-image .el-image){width:150px;height:130px}@media(max-width:600px){.todo-message{gap:8px}.todo-message-content{max-width:calc(100% - 42px)}.todo-bubble{padding:12px}.todo-conversation :deep(.todo-image .el-image){width:100px;height:90px}.todo-action{padding:12px}.todo-action-buttons .el-button{margin-left:0}}
</style>
