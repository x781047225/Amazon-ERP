<script setup lang="ts">
import {ref,reactive,computed,onMounted,onBeforeUnmount} from 'vue'
import {api,can} from './session'
import {ElMessage,ElMessageBox} from 'element-plus'
import CustomsFields from './CustomsFields.vue'
import {defaults} from './customsData'
const props=defineProps<{profileId?:number}>(),emit=defineEmits<{saved:[data:{id:number,name:string}],close:[]}>()
const editable=computed(()=>can('products.declaration','operate')||can('products.clearance','operate'))
const busy=ref(false),ready=ref(false),error=ref(''),id=ref(props.profileId),formRef=ref<any>()
const form=reactive<any>({name:'',declaration:defaults(),clearance:defaults(),revision:null})
const rules={name:[{required:true,whitespace:true,message:'请填写商品名称',trigger:'blur'}]}
let baseline=''
const state=()=>JSON.stringify(form)
function populate(p:any){Object.assign(form,{name:p.name,declaration:{...p.declaration},clearance:{...p.clearance},revision:p.revision});for(const k of ['declaration','clearance'])for(const f of ['tax_rate','unit_price'])form[k][f]=form[k][f]==null?null:Number(form[k][f]);baseline=state()}
async function load(){busy.value=true;try{if(id.value)populate(await api('/customs/profiles/'+id.value));else baseline=state();ready.value=true;error.value=''}catch(e){error.value=(e as Error).message}finally{busy.value=false}}
async function save(){if(busy.value||!ready.value||!editable.value)return;if(!await formRef.value.validate().catch(()=>false))return;busy.value=true;error.value='';try{const p=await api('/customs/profiles'+(id.value?'/'+id.value:''),{name:form.name,declaration:form.declaration,clearance:form.clearance,revision:form.revision});id.value=p.id;populate(p);emit('saved',{id:p.id,name:p.name});ElMessage.success('资料已保存')}catch(e){error.value=(e as Error).message;ElMessage.error(error.value)}finally{busy.value=false}}
async function canLeave(){if(busy.value)return false;if(!ready.value||baseline===state())return true;try{await ElMessageBox.confirm('尚有未保存的清关报关资料，确定放弃修改？','离开编辑',{type:'warning',confirmButtonText:'放弃修改',cancelButtonText:'继续编辑'});return true}catch{return false}}
function unload(e:BeforeUnloadEvent){if(ready.value&&baseline!==state()){e.preventDefault();e.returnValue=''}}
defineExpose({canLeave})
onMounted(()=>{load();window.addEventListener('beforeunload',unload)})
onBeforeUnmount(()=>window.removeEventListener('beforeunload',unload))
</script>
<template><section class="customs-profile-editor" v-loading="busy">
 <div class="customs-editor-toolbar"><h2>{{id?(editable?'编辑':'查看'):'新增'}}清关&amp;报关资料</h2><div><el-button @click="emit('close')">关闭</el-button><el-button v-if="editable" type="primary" :loading="busy" :disabled="!ready" @click="save">保存资料</el-button></div></div>
 <p class="form-hint"><span class="required-mark">*</span> 为必填项，其他字段可选填。</p>
 <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon/>
 <el-button v-if="!ready&&!busy" @click="load">重新加载</el-button>
 <el-form ref="formRef" :model="form" :rules="rules" label-position="top" :disabled="!editable||busy||!ready" @submit.prevent="save">
  <el-form-item label="商品名称" prop="name" required><el-input v-model="form.name" aria-label="资料商品名称" aria-required="true" placeholder="必填：请输入商品名称" maxlength="500"/></el-form-item>
  <h3>基础资料</h3><CustomsFields :model-value="form.declaration" kind="declaration" mode="shared"/>
  <h3>报关资料</h3><CustomsFields :model-value="form.declaration" kind="declaration" mode="specific"/>
  <h3>清关资料</h3><CustomsFields :model-value="form.clearance" kind="clearance" mode="specific"/>
 </el-form>
 <div class="customs-editor-footer"><el-button @click="emit('close')">关闭</el-button><el-button v-if="editable" type="primary" :loading="busy" :disabled="!ready" @click="save">保存资料</el-button></div>
</section></template>
<style scoped>
.customs-profile-editor{background:white;padding:22px 28px;max-width:1280px;margin:20px auto;border:1px solid #dce6e1;border-radius:8px;box-sizing:border-box}.customs-editor-toolbar{display:flex;align-items:center;justify-content:space-between;gap:16px;padding-bottom:18px;border-bottom:1px solid #e5ece8;margin-bottom:24px}.customs-editor-toolbar h2{font-size:18px}.customs-editor-toolbar>div{display:flex;flex-shrink:0}.customs-profile-editor h3{border-top:1px solid #e4eae7;padding-top:20px;margin-top:24px}.form-hint{font-size:13px;color:#7a8c84}.required-mark{color:#f56c6c}.customs-profile-editor .el-alert{margin-bottom:18px}.customs-editor-footer{display:flex;justify-content:flex-end;position:sticky;bottom:0;background:white;padding:14px 0;border-top:1px solid #e5ece8;z-index:2}@media(max-width:650px){.customs-profile-editor{padding:18px 16px}.customs-editor-toolbar{flex-wrap:wrap}}
</style>
