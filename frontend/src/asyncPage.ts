import {defineAsyncComponent,defineComponent,h,type Component,type AsyncComponentLoader} from 'vue'
const Loading=defineComponent({setup:()=>()=>h('div',{role:'status',style:'padding:36px;color:#6c7f77'},'正在加载页面…')})
const Failed=defineComponent({setup:()=>()=>h('div',{role:'alert',style:'padding:36px'},[
 h('p','页面加载失败，可能是网络中断或系统刚刚更新。'),
 h('p','刷新前请先保存其他标签页中正在编辑的内容。'),
 h('button',{class:'el-button el-button--primary',onClick:()=>window.location.reload()},'刷新并重新加载')
])})
export function asyncPage<T extends Component>(loader:AsyncComponentLoader<T>){return defineAsyncComponent<T>({loader,loadingComponent:Loading,errorComponent:Failed,delay:150,timeout:30000,onError(_error,retry,fail,attempts){if(attempts<3)setTimeout(retry,600*attempts);else fail()}})}
