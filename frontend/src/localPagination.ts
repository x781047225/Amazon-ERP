import {computed,ref,watch} from 'vue'

// Paginate already loaded management lists without changing their API contracts.
export function useLocalPagination<T>(source:()=>T[]){
 const page=ref(1),pageSize=ref(25)
 const all=computed(source)
 const total=computed(()=>all.value.length)
 const rows=computed(()=>all.value.slice((page.value-1)*pageSize.value,page.value*pageSize.value))
 watch(all,()=>{page.value=1})
 return {page,pageSize,total,rows}
}
