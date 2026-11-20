import {onMounted,onUnmounted,type Ref} from 'vue'
/** CSS keeps the header in normal table flow; only measure the filter row offset. */
export function useAdsSticky(root:Ref<HTMLElement|null>){
 let observer:ResizeObserver|undefined,mutations:MutationObserver|undefined,filter:HTMLElement|null=null
 const measure=()=>{const panel=root.value;if(!panel)return;const next=panel.querySelector<HTMLElement>('.ads-filters');if(next!==filter){if(filter)observer?.unobserve(filter);filter=next;if(filter)observer?.observe(filter)}const value=46+(filter?.offsetHeight||0)+'px';if(panel.style.getPropertyValue('--ads-sticky-top')!==value)panel.style.setProperty('--ads-sticky-top',value)}
 onMounted(()=>{observer=new ResizeObserver(measure);mutations=new MutationObserver(measure);if(root.value)mutations.observe(root.value,{childList:true,subtree:true});measure()})
 onUnmounted(()=>{observer?.disconnect();mutations?.disconnect()})
}
