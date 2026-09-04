<template>
  <div class="current-group-select">
    <SearchableSelect
      :model-value="selectedGroupId"
      :label="label"
      :description="description"
      :options="groupOptions"
      :required="required"
      :disabled="!botId || groupsQuery.isPending.value"
      :placeholder="botId ? '搜索或选择当前 Bot 的群' : '请先选择 Bot'"
      @update:model-value="selectGroup"
    />
    <p v-if="groupsQuery.isError.value" class="form-error" role="alert">
      当前 Bot 的群目录暂时不可读取；不会清除已保存的选择。
    </p>
    <p v-else-if="botId && !groupsQuery.isPending.value && !groupOptions.length" class="form-description">
      当前 Bot 没有可选择的已确认或已配置群。
    </p>
  </div>
</template>

<script setup lang="ts">
import SearchableSelect from "@vue-app/components/forms/SearchableSelect.vue";
import { useCurrentGroupSelection } from "@vue-app/composables/currentGroup";

const props = withDefaults(defineProps<{
  label?: string;
  description?: string;
  required?: boolean;
  allowEmpty?: boolean;
  emptyLabel?: string;
}>(), {
  label: "当前 Bot 群",
  description: "群选择会随当前 Bot 分开保存，并同步到地址栏。",
  required: false,
  allowEmpty: false,
  emptyLabel: "不限定群",
});

const { botId, selectedGroupId, groupOptions, groupsQuery, selectGroup } = useCurrentGroupSelection({
  allowEmpty: props.allowEmpty,
  emptyLabel: props.emptyLabel,
});
</script>
