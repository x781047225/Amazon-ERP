import {ref,watch} from 'vue'
const saved=localStorage.getItem('erp-home-currency')||'EUR'
export const displayCurrency=ref(['GBP','EUR','CNY'].includes(saved)?saved:'EUR')
watch(displayCurrency,c=>localStorage.setItem('erp-home-currency',c))
