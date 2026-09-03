<template>
  <Panel eyebrow="GROUP / MEMBERS" :title="`成员画像（${profiles.length}）`">
    <div class="trace-table-wrap">
      <table class="forensic-table">
        <thead>
          <tr>
            <th>成员</th>
            <th>昵称</th>
            <th>好感</th>
            <th>关系</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(item, index) in profiles" :key="text(item.user_id, String(index))">
            <td><code>{{ text(item.user_id) }}</code></td>
            <td>{{ text(item.nickname ?? item.card) }}</td>
            <td :class="favorabilityDisplayClass(getMemberScore(item))">
              {{ text(record(item.favorability).level ?? item.favorability_level, "未分级") }} · {{ text(getMemberScore(item), "0") }}
            </td>
            <td>{{ text(item.relationship) }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </Panel>
</template>

<script setup lang="ts">
import Panel from "@vue-app/components/Panel.vue";
import { favorabilityDisplayClass, type JsonRecord, record, text } from "./managementHelpers";

withDefaults(defineProps<{ profiles?: JsonRecord[] }>(), {
  profiles: () => [],
});

function getMemberScore(member: JsonRecord) {
  const fav = record(member.favorability);
  return fav.score ?? member.favorability_score ?? 0;
}
</script>
