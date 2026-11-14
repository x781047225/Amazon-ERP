<script setup lang="ts">
import {ref,onMounted,onUnmounted} from 'vue'
import {Box,User,Lock} from '@element-plus/icons-vue'
import Workspace from './Workspace.vue'
import {session,api} from './session'
const ready=ref(false),busy=ref(false),username=ref('admin'),password=ref(''),error=ref('')
function expired(){session.value=null;password.value='';error.value='登录已失效，请重新登录'}
async function login(){if(!username.value||!password.value)return;busy.value=true;error.value='';try{session.value=await api('/auth/login',{username:username.value,password:password.value});password.value=''}catch(e){error.value=(e as Error).message}finally{busy.value=false}}
onMounted(async()=>{try{const r=await fetch('/api/auth/me');if(r.ok)session.value=await r.json()}finally{ready.value=true}window.addEventListener('erp:unauthorized',expired)})
onUnmounted(()=>window.removeEventListener('erp:unauthorized',expired))
</script>
<template>
 <div v-if="!ready" class="login-loading">正在打开工作空间…</div>
 <Workspace v-else-if="session" :key="session.id"/>
 <main v-else class="login-page"><section class="login-story"><div class="login-brand"><el-icon><Box/></el-icon> Amazon ERP</div><span class="eyebrow">YOUR BUSINESS, IN FOCUS</span><h1>从一家店铺，<br>看清每一天的经营。</h1><p>订单、商品、库存与回款，<br>在同一个工作空间里有序衔接。</p><div class="login-pills"><span>店铺权限</span><span>本地存储</span><span>按需同步</span></div></section><section class="login-card"><span class="eyebrow">WELCOME BACK</span><h2>登录工作空间</h2><p class="muted">使用管理员为你分配的账号</p><form @submit.prevent="login"><label>账号</label><el-input v-model="username" aria-label="账号" autocomplete="username" :prefix-icon="User" size="large" maxlength="64"/><label>密码</label><el-input v-model="password" aria-label="密码" type="password" autocomplete="current-password" show-password :prefix-icon="Lock" size="large" maxlength="128"/><el-alert v-if="error" :title="error" type="error" :closable="false"/><el-button type="primary" size="large" native-type="submit" :loading="busy">登录</el-button></form><small>忘记密码？请联系管理员重置。</small></section></main>
</template>
