<script setup lang="ts">
import {computed,ref,watch,onMounted} from 'vue'
import {Search} from '@element-plus/icons-vue'
import {searchHelp} from './helpContent'
onMounted(()=>{document.title='帮助文档 · Amazon ERP';searchInput.value?.focus()})
const query=ref(''),selected=ref('start'),searchInput=ref<any>(null)
const results=computed(()=>searchHelp(query.value)),article=computed(()=>results.value.find(a=>a.id===selected.value)||results.value[0])
watch(results,items=>{if(!items.some(a=>a.id===selected.value))selected.value=items[0]?.id||''})
</script>
<template>
 <main class="system-help"><header class="help-page-heading"><div><span>AMAZON ERP · 帮助中心</span><h1>系统帮助文档</h1></div><p>功能说明与常见问题</p></header>
  <el-input ref="searchInput" v-model="query" clearable :prefix-icon="Search" placeholder="搜索功能或问题，例如：库存刷新、批量竞价、采购备注" aria-label="搜索帮助文档"/>
  <div class="help-count" role="status">{{query.trim()?`找到 ${results.length} 篇相关说明`:`共 ${results.length} 篇使用说明`}}</div>
  <div v-if="article" class="help-layout">
   <nav class="help-index" aria-label="帮助文档目录"><button v-for="item in results" :key="item.id" :aria-current="article.id===item.id?'page':undefined" :class="{selected:article.id===item.id}" @click="selected=item.id"><small>{{item.category}}</small><span>{{item.title}}</span></button></nav>
   <article :key="article.id" class="help-article" tabindex="0"><div class="help-category">{{article.category}}</div><h2>{{article.title}}</h2><p class="help-intro">{{article.intro}}</p><section v-for="s in article.sections" :key="s.title"><h3>{{s.title}}</h3><p v-for="p in s.paragraphs" :key="p">{{p}}</p><ol v-if="s.steps"><li v-for="step in s.steps" :key="step">{{step}}</li></ol><p v-if="s.example" class="help-example"><strong>虚构示例</strong>{{s.example}}</p></section></article>
  </div>
  <el-empty v-else description="没有找到相关说明，试试更短的关键词"/>
  </main>
</template>
<style scoped>
.system-help{box-sizing:border-box;max-width:1320px;margin:0 auto;padding:32px;background:#fff;min-height:100vh}.help-page-heading{display:flex;justify-content:space-between;align-items:center;margin-bottom:24px;gap:20px}.help-page-heading span{color:#688677;font-size:12px;letter-spacing:1px}.help-page-heading h1{font-size:28px;color:#263e34;margin:6px 0}.help-page-heading p{color:#829189;font-size:14px}

.help-count{font-size:12px;color:#829189;margin:12px 0}.help-layout{display:grid;grid-template-columns:255px minmax(0,1fr);gap:24px;height:calc(100dvh - 220px);min-height:280px}.help-index{overflow:auto;border-right:1px solid #e5ece8;padding-right:14px}.help-index button{display:block;width:100%;border:0;background:transparent;border-radius:7px;padding:10px 12px;text-align:left;color:#40574d;cursor:pointer;margin-bottom:5px}.help-index button:hover{background:#f1f6f3}.help-index button.selected{background:#e6f2ed;color:#176b55}.help-index button:focus-visible{outline:2px solid #21745f;outline-offset:-2px}.help-index small{display:block;color:#87998f;font-size:11px;margin-bottom:4px}.help-index span{font-size:14px;line-height:1.6}.help-article{overflow:auto;padding:0 16px 16px 0;line-height:1.85;color:#40574d}.help-category{font-size:12px;color:#789385}.help-article h2{font-size:24px;line-height:1.5;color:#263e34;margin:8px 0 14px}.help-intro{color:#718378}.help-article h3{font-size:16px;color:#263e34;margin:24px 0 8px}.help-article p{margin:8px 0}.help-article ol{padding-left:22px}.help-article li{padding-left:5px;margin:8px 0}.help-example{background:#f3f7f5;border-left:3px solid #94b7a5;padding:12px 16px;border-radius:4px}.help-example strong{display:block;font-size:12px;color:#748d7e}@media(max-width:700px){.system-help{padding:16px}.help-page-heading{display:block;margin-bottom:16px}.help-page-heading h1{font-size:24px}.help-page-heading p{margin:6px 0}.help-layout{grid-template-columns:1fr;gap:12px;height:calc(100dvh - 220px);grid-template-rows:130px minmax(0,1fr)}.help-index{border-right:0;border-bottom:1px solid #e5ece8}.help-article h2{font-size:20px}}
</style>
