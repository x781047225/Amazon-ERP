<script setup lang="ts">
defineProps<{label:string,description:string}>()
</script>
<template>
 <el-tooltip :content="description" placement="top" popper-class="ads-column-help-tip" :show-after="150">
  <button type="button" class="ads-column-help" :aria-label="label+'说明'" @click.stop @mousedown.stop>!</button>
 </el-tooltip>
</template>
<style scoped>
.ads-column-help{display:inline-flex;align-items:center;justify-content:center;vertical-align:middle;width:14px;height:14px;margin-left:5px;padding:0;border:1px solid #92a69c;border-radius:50%;color:#83988c;background:transparent;font-size:10px;line-height:1;font-weight:600;cursor:help;flex-shrink:0}.ads-column-help:focus-visible{outline:2px solid #227660;outline-offset:2px}
</style>
<style>.ads-column-help-tip{max-width:360px;white-space:pre-line;line-height:1.7}</style>
