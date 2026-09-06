<template>
  <nav v-if="totalPages > 1" class="pagination shared-pagination" aria-label="分页导航">
    <button type="button" :disabled="disabled || safePage <= 1" aria-label="首页" @click="emitPage(1)">首页</button>
    <button type="button" :disabled="disabled || safePage <= 1" aria-label="上一页" @click="emitPage(safePage - 1)">上一页</button>
    <label class="pagination-current">
      <span class="sr-only">跳转页码</span>
      <input v-model="jumpPage" type="number" inputmode="numeric" min="1" :max="totalPages" :disabled="disabled" @keydown.enter.prevent="jump" @blur="jump" />
      <span>/ {{ totalPages }} 页</span>
    </label>
    <span v-if="total >= 0" class="pagination-total">共 {{ total }} 条</span>
    <button type="button" :disabled="disabled || safePage >= totalPages" aria-label="下一页" @click="emitPage(safePage + 1)">下一页</button>
    <button type="button" :disabled="disabled || safePage >= totalPages" aria-label="末页" @click="emitPage(totalPages)">末页</button>
  </nav>
</template>

<script setup lang="ts">
import { computed, ref, watch } from "vue";

const props = withDefaults(defineProps<{
  page: number;
  totalPages: number;
  total?: number;
  disabled?: boolean;
}>(), { total: -1, disabled: false });

const emit = defineEmits<{ "update:page": [page: number] }>();
const safePage = computed(() => Math.max(1, Math.min(Math.trunc(props.page) || 1, Math.max(1, props.totalPages))));
const jumpPage = ref(String(safePage.value));
watch(safePage, (value) => { jumpPage.value = String(value); }, { immediate: true });
watch([() => props.page, () => props.totalPages, () => props.disabled], () => {
  if (!props.disabled && props.page !== safePage.value) emit("update:page", safePage.value);
}, { flush: "post" });

function emitPage(candidate: number): void {
  if (props.disabled) return;
  const next = Math.max(1, Math.min(Math.trunc(candidate) || 1, Math.max(1, props.totalPages)));
  jumpPage.value = String(next);
  if (next !== safePage.value) emit("update:page", next);
}
function jump(): void { if (!props.disabled) emitPage(Number(jumpPage.value)); }
</script>
