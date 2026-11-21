import {createApp,defineAsyncComponent} from 'vue'
import ElementPlus from 'element-plus'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import 'element-plus/dist/index.css'
import './style.css'
import App from './App.vue'
import TableReset from './TableReset.vue'
import AppPagination from './AppPagination.vue'
createApp(window.location.pathname==='/help'?defineAsyncComponent(()=>import('./HelpPage.vue')):App).component('TableReset',TableReset).component('AppPagination',AppPagination).use(ElementPlus,{locale:zhCn}).mount('#app')
