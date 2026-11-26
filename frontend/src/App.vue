<script setup lang="ts">
import {ref,onMounted,onUnmounted} from 'vue'
import {Box,User,Lock} from '@element-plus/icons-vue'
import Workspace from './Workspace.vue'
import {session,api} from './session'
const ready=ref(false),busy=ref(false),username=ref('admin'),password=ref(''),error=ref(''),rememberMe=ref(false)
function expired(){session.value=null;password.value='';error.value='登录已失效，请重新登录'}
async function login(){if(busy.value||!username.value||!password.value)return;busy.value=true;error.value='';try{session.value=await api('/auth/login',{username:username.value,password:password.value,remember_me:rememberMe.value});password.value=''}catch(e){error.value=(e as Error).message}finally{busy.value=false}}
onMounted(async()=>{try{const r=await fetch('/api/auth/me');if(r.ok)session.value=await r.json()}catch{error.value='暂时无法连接服务器，请稍后重试'}finally{ready.value=true}window.addEventListener('erp:unauthorized',expired)})
onUnmounted(()=>window.removeEventListener('erp:unauthorized',expired))
</script>
<template>
 <div v-if="!ready" class="startup-loading" role="status"><div class="startup-card"><span class="startup-brand">RICH-ERP</span><h1>正在打开工作空间</h1><p>你的经营概览即将呈现</p><div class="startup-track" aria-hidden="true"><i/></div></div></div>
 <Workspace v-else-if="session" :key="session.id"/>
 <main v-else class="login-page"><section class="login-story"><div class="login-brand"><el-icon><Box/></el-icon> Amazon ERP</div><span class="eyebrow">YOUR BUSINESS, IN FOCUS</span><h1>从一家店铺，<br>看清每一天的经营。</h1><p>订单、商品、库存与回款，<br>在同一个工作空间里有序衔接。</p><div class="login-pills"><span>店铺权限</span><span>本地存储</span><span>按需同步</span></div></section><section class="login-card"><span class="eyebrow">WELCOME BACK</span><h2>登录工作空间</h2><p class="muted">使用管理员为你分配的账号</p><form @submit.prevent="login"><label>账号</label><el-input v-model="username" aria-label="账号" autocomplete="username" :prefix-icon="User" size="large" maxlength="64"/><label>密码</label><el-input v-model="password" aria-label="密码" type="password" autocomplete="current-password" show-password :prefix-icon="Lock" size="large" maxlength="128"/><el-checkbox v-model="rememberMe" :disabled="busy" class="remember-login">记住登录 30 天</el-checkbox><small class="login-session-hint">{{rememberMe?'本设备 30 天内免登录，不保存密码。':'未勾选时，登录后 12 小时失效。'}}</small><el-alert v-if="error" :title="error" type="error" :closable="false"/><el-button type="primary" size="large" native-type="submit" :loading="busy">登录</el-button></form><small>忘记密码？请联系管理员重置。</small></section></main>
</template>

<style scoped>.login-card .remember-login{margin-top:4px;font-weight:400}.login-session-hint{line-height:1.5;margin-top:-8px}</style>
