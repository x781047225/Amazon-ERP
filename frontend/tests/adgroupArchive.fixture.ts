import {createApp,h} from 'vue'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import AdvertisingPanel from '../src/AdvertisingPanel.vue'
import {session} from '../src/session'
session.value={id:999,username:'adgroup-fixture',is_admin:true,active:true,stores:['Demo'],default_store:'Demo',current_store:'Demo'}
createApp({render:()=>h(AdvertisingPanel,{context:{profileId:'test',store:'Demo',country:'UK',campaign:{id:'c1',name:'模拟活动'}},active:true})}).use(ElementPlus).mount('#app')
