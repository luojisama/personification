<template>
  <div v-if="open" class="dialog-overlay" @mousedown.self="cancel">
    <section
      ref="dialogElement"
      class="dialog confirm-dialog"
      :class="{ 'confirm-dialog-danger': dangerous }"
      role="dialog"
      aria-modal="true"
      :aria-labelledby="titleId"
      :aria-describedby="description ? descriptionId : undefined"
      tabindex="-1"
      @keydown="onKeydown"
    >
      <header class="dialog-header">
        <h2 :id="titleId">{{ title }}</h2>
        <button class="dialog-close" type="button" aria-label="关闭确认对话框" @click="cancel">×</button>
      </header>
      <div class="dialog-body">
        <p v-if="description" :id="descriptionId">{{ description }}</p>
        <slot />
      </div>
      <footer class="dialog-footer">
        <button ref="cancelButton" class="button button-secondary" type="button" @click="cancel">{{ cancelLabel }}</button>
        <button ref="confirmButton" :class="['button', dangerous ? 'button-danger' : 'button-primary']" type="button" @click="confirm">{{ confirmLabel }}</button>
      </footer>
    </section>
  </div>
</template>

<script setup lang="ts">
import { nextTick, onBeforeUnmount, ref, useId, watch } from "vue";

const props = withDefaults(defineProps<{
  open: boolean;
  title: string;
  description?: string;
  confirmLabel?: string;
  cancelLabel?: string;
  dangerous?: boolean;
}>(), {
  description: "",
  confirmLabel: "确认",
  cancelLabel: "取消",
  dangerous: false,
});

const emit = defineEmits<{
  "update:open": [value: boolean];
  confirm: [];
  cancel: [];
}>();
const generatedId = useId();
const titleId = `confirm-dialog-${generatedId}-title`;
const descriptionId = `confirm-dialog-${generatedId}-description`;
const dialogElement = ref<HTMLElement | null>(null);
const cancelButton = ref<HTMLButtonElement | null>(null);
const confirmButton = ref<HTMLButtonElement | null>(null);
let previousFocus: HTMLElement | null = null;

watch(() => props.open, async (isOpen) => {
  if (isOpen) {
    previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    await nextTick();
    (props.dangerous ? cancelButton.value : confirmButton.value)?.focus();
    return;
  }
  restoreFocus();
}, { immediate: true });

onBeforeUnmount(restoreFocus);

function restoreFocus(): void {
  previousFocus?.focus();
  previousFocus = null;
}

function cancel(): void {
  emit("update:open", false);
  emit("cancel");
}

function confirm(): void {
  emit("confirm");
}

function focusableElements(): HTMLElement[] {
  return dialogElement.value
    ? Array.from(dialogElement.value.querySelectorAll<HTMLElement>('button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'))
    : [];
}

function onKeydown(event: KeyboardEvent): void {
  if (event.key === "Escape") {
    event.preventDefault();
    cancel();
    return;
  }
  if (event.key !== "Tab") return;
  const elements = focusableElements();
  if (!elements.length) {
    event.preventDefault();
    dialogElement.value?.focus();
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
