<template>
  <div class="collapsible-text">
    <pre v-if="expanded || text.length <= limit" class="collapsible-text-content">{{ text }}</pre>
    <p v-else class="collapsible-text-preview">{{ text.slice(0, limit) }}…</p>
    <span v-if="!expanded && text.length > limit" hidden="until-found" @beforematch="expanded = true">{{ text }}</span>
    <div v-if="text.length > limit" class="collapsible-text-actions">
      <button type="button" class="text-button" :aria-expanded="expanded" @click="expanded = !expanded">{{ expanded ? "收起" : "展开全文" }}</button>
      <button type="button" class="text-button" @click="copy">复制全文</button>
      <span v-if="copyStatus" class="collapsible-copy-status" role="status" aria-live="polite">{{ copyStatus }}</span>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, watch } from "vue";
const props = withDefaults(defineProps<{ text: string; limit?: number }>(), { limit: 280 });
const expanded = ref(false);
const copyStatus = ref("");
watch(() => props.text, () => { expanded.value = false; copyStatus.value = ""; });
async function copy(): Promise<void> {
  try {
    if (!navigator.clipboard?.writeText) throw new Error("clipboard unavailable");
    await navigator.clipboard.writeText(props.text);
    copyStatus.value = "已复制全文";
  } catch { copyStatus.value = "复制失败，请手动选择全文"; }
}
</script>
