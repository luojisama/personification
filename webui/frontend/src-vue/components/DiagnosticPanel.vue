<template>
  <Panel :eyebrow="`DIAGNOSTIC / ${diagnostic.phase ? diagnostic.phase.toUpperCase() : 'UNKNOWN'}`" :title="diagnostic.title || diagnosticCodeLabel(diagnostic.code)">
    <template #actions><StateBadge :tone="tone">{{ statusLabel }}</StateBadge></template>
    <div class="diagnostic-content">
      <p v-if="diagnostic.message" class="diagnostic-message">{{ diagnostic.message }}</p>
      <p v-if="diagnostic.suggestion" class="diagnostic-suggestion">{{ diagnostic.suggestion }}</p>
      <dl class="compact-kv">
        <dt>诊断码</dt><dd><code>{{ diagnostic.code }}</code></dd>
        <template v-if="diagnostic.operation_id"><dt>Operation ID</dt><dd><code>{{ diagnostic.operation_id }}</code></dd></template>
        <template v-if="diagnostic.trace_id"><dt>Trace ID</dt><dd><code>{{ diagnostic.trace_id }}</code></dd></template>
        <dt>可重试</dt><dd>{{ diagnostic.retryable ? "是" : "否" }}</dd>
      </dl>
      <div v-if="diagnostic.warnings?.length" class="diagnostic-warnings">
        <strong>警告</strong><ul><li v-for="(warning, index) in diagnostic.warnings" :key="index">{{ warning }}</li></ul>
      </div>
      <div v-if="diagnostic.steps?.length" class="diagnostic-steps">
        <strong>步骤详情</strong><ol><li v-for="step in diagnostic.steps" :key="step.key"><span>{{ step.label }}（{{ step.status }}）</span><p v-if="step.message">{{ step.message }}</p></li></ol>
      </div>
    </div>
  </Panel>
</template>

<script setup lang="ts">
import { computed } from "vue";
import { diagnosticCodeLabel } from "@/api/diagnostics";
import type { OperationDiagnostic } from "@/api/types";
import Panel from "@vue-app/components/Panel.vue";
import StateBadge from "@vue-app/components/StateBadge.vue";

const props = defineProps<{ diagnostic: OperationDiagnostic; defaultOpen?: boolean }>();
const tone = computed<"ok" | "warn" | "error" | "running" | "unknown">(() => {
  if (props.diagnostic.ok) return "ok";
  if (props.diagnostic.outcome_unknown) return "unknown";
  if (props.diagnostic.partial) return "warn";
  return "error";
});
const statusLabel = computed(() => props.diagnostic.ok ? "正常" : props.diagnostic.outcome_unknown ? "未知结果" : props.diagnostic.partial ? "部分完成" : "失败");
</script>
