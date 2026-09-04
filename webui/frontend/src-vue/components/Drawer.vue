<template>
  <div v-if="open" class="drawer-root" @keydown="onKeydown">
    <button v-if="modal" class="drawer-scrim" type="button" aria-label="关闭侧边栏" @click="close" />
    <aside
      ref="drawerElement"
      :class="['drawer-panel', `drawer-panel-${side}`]"
      role="dialog"
      :aria-modal="modal || undefined"
      :aria-labelledby="titleId"
      tabindex="-1"
    >
      <header class="drawer-header">
        <h2 :id="titleId">{{ title }}</h2>
        <button ref="closeButton" class="drawer-close" type="button" :aria-label="`关闭${title}`" @click="close">×</button>
      </header>
      <div class="drawer-body"><slot /></div>
    </aside>
  </div>
</template>

<script setup lang="ts">
import { nextTick, onBeforeUnmount, ref, useId, watch } from "vue";

const props = withDefaults(defineProps<{
  open: boolean;
  title: string;
  side?: "left" | "right";
  modal?: boolean;
}>(), {
  side: "left",
  modal: true,
});

const emit = defineEmits<{
  "update:open": [value: boolean];
  close: [];
}>();
const generatedId = useId();
const titleId = `drawer-${generatedId}-title`;
const drawerElement = ref<HTMLElement | null>(null);
const closeButton = ref<HTMLButtonElement | null>(null);
let previousFocus: HTMLElement | null = null;

watch(() => props.open, async (isOpen) => {
  if (isOpen) {
    previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    await nextTick();
    closeButton.value?.focus();
    return;
  }
  restoreFocus();
}, { immediate: true });

onBeforeUnmount(restoreFocus);

function restoreFocus(): void {
  previousFocus?.focus();
  previousFocus = null;
}

function close(): void {
  emit("update:open", false);
  emit("close");
}

function focusableElements(): HTMLElement[] {
  return drawerElement.value
    ? Array.from(drawerElement.value.querySelectorAll<HTMLElement>('button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'))
    : [];
}

function onKeydown(event: KeyboardEvent): void {
  if (event.key === "Escape") {
    event.preventDefault();
    close();
    return;
  }
  if (!props.modal || event.key !== "Tab") return;
  const elements = focusableElements();
  if (!elements.length) {
    event.preventDefault();
    drawerElement.value?.focus();
    return;
  }
  const firstElement = elements[0];
  const lastElement = elements[elements.length - 1];
  if (event.shiftKey && document.activeElement === firstElement) {
    event.preventDefault();
    lastElement?.focus();
  } else if (!event.shiftKey && document.activeElement === lastElement) {
    event.preventDefault();
    firstElement?.focus();
  }
}
</script>
