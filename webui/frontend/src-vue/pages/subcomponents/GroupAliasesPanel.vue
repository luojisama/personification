<template>
  <Panel eyebrow="GROUP / ALIASES" :title="`成员别名（${aliases.length}）`">
    <div class="trace-table-wrap">
      <table class="forensic-table">
        <thead>
          <tr>
            <th>成员 ID</th>
            <th>称呼</th>
            <th>备注</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(item, index) in aliases" :key="text(item.user_id, String(index))">
            <td><code>{{ text(item.user_id) }}</code></td>
            <td>{{ text(item.aliases) }}</td>
            <td>{{ text(item.note) }}</td>
          </tr>
        </tbody>
      </table>
    </div>
    <div class="form-grid">
      <TextField :model-value="memberId" label="成员 QQ" inputmode="numeric" @update:model-value="$emit('update:memberId', $event)" />
      <TextField :model-value="aliasText" label="称呼（逗号分隔）" @update:model-value="$emit('update:aliasText', $event)" />
    </div>
    <div class="inline-controls">
      <button class="button" type="button" :disabled="!memberId || !aliasText || pending" @click="$emit('save')">
        保存别名
      </button>
      <button class="button button-danger" type="button" :disabled="!memberId || pending" @click="$emit('delete')">
        删除别名
      </button>
    </div>
  </Panel>
</template>

<script setup lang="ts">
import Panel from "@vue-app/components/Panel.vue";
import TextField from "@vue-app/components/forms/TextField.vue";
import { type JsonRecord, text } from "./managementHelpers";

withDefaults(
  defineProps<{
    aliases?: JsonRecord[];
    memberId?: string;
    aliasText?: string;
    pending?: boolean;
  }>(),
  {
    aliases: () => [],
    memberId: "",
    aliasText: "",
    pending: false,
  },
);

defineEmits<{ (e: "update:memberId", value: string): void; (e: "update:aliasText", value: string): void; (e: "save"): void; (e: "delete"): void }>();
</script>
