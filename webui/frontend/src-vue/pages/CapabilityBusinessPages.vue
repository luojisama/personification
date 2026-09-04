<template>
  <div class="page-stack">
    <!-- 1. Skill 管理 -->
    <template v-if="currentMode === 'skills'">
      <PageHeader
        index="19"
        title="Skill 管理"
        description="已安装 Skill、远程源、审核状态和健康信息分层展示；启停、远程加载与重载都有明确诊断。"
      >
        <template #actions>
          <div class="search-field">
            <input
              v-model="skillSearch"
              type="search"
              placeholder="搜索 Skill 名称或描述"
              aria-label="搜索 Skill 名称或描述"
              @input="skillPage = 1"
            />
          </div>
        </template>
      </PageHeader>

      <Panel
        v-if="currentSection === 'remote'"
        eyebrow="SKILL / REMOTE"
        title="远程来源控制"
      >
        <p>远程来源仍需内容 digest 审核；打开加载开关不会自动批准或执行外部代码。</p>
        <div class="inline-controls">
          <button
            class="button button-primary"
            type="button"
            :disabled="skillRemoteToggleMutation.isPending.value"
            @click="handleRemoteToggle(true)"
          >
            开启远程加载
          </button>
          <button
            class="button button-secondary"
            type="button"
            :disabled="skillRemoteToggleMutation.isPending.value"
            @click="handleRemoteToggle(false)"
          >
            关闭远程加载
          </button>
        </div>
      </Panel>

      <Panel
        :eyebrow="`SKILL / ${currentSection.toUpperCase()}`"
        :title="currentSection === 'health' ? '健康与审核' : '已安装 Skill'"
      >
        <template #actions>
          <button
            class="button button-secondary"
            type="button"
            :disabled="skillReloadMutation.isPending.value"
            @click="handleSkillReload"
          >
            重载 Runtime
          </button>
        </template>

        <QueryBoundary
          :pending="skillsQuery.isPending.value"
          :error="skillsQuery.error.value"
          :empty="skillRows.length === 0"
          empty-text="当前没有匹配的 Skill。"
        >
          <div class="table-responsive">
            <table class="data-table">
              <thead>
                <tr>
                  <th scope="col">Skill</th>
                  <th scope="col">来源</th>
                  <th scope="col">副作用</th>
                  <th scope="col">状态</th>
                  <th scope="col">操作</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="(row, idx) in skillRows" :key="textAt(row, 'name') + idx">
                  <td>
                    <strong>{{ textAt(row, 'name') }}</strong>
                    <br />
                    <span class="muted">{{ textAt(row, 'description') }}</span>
                  </td>
                  <td>{{ textAt(row, 'source_kind', 'category') }}</td>
                  <td>{{ textAt(row, 'risk', 'side_effect_level', 'permission') }}</td>
                  <td>
                    <StateBadge
                      :tone="row.user_disabled === true || row.health_disabled === true ? 'error' : 'ok'"
                    >
                      {{ row.user_disabled === true ? '用户禁用' : row.health_disabled === true ? '健康禁用' : '已启用' }}
                    </StateBadge>
                  </td>
                  <td>
                    <button
                      class="button button-secondary"
                      type="button"
                      :disabled="skillToggleMutation.isPending.value"
                      @click="handleSkillToggle(row)"
                    >
                      {{ row.user_disabled === true ? '启用' : '禁用' }}
                    </button>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </QueryBoundary>

        <div v-if="skillsQuery.data.value && skillsQuery.data.value.total_pages > 1" class="pagination">
          <button
            type="button"
            :disabled="skillPage <= 1"
            @click="skillPage--"
          >
            上一页
          </button>
          <span>第 {{ skillsQuery.data.value.page }} / {{ skillsQuery.data.value.total_pages }} 页</span>
          <button
            type="button"
            :disabled="skillPage >= skillsQuery.data.value.total_pages"
            @click="skillPage++"
          >
            下一页
          </button>
        </div>
      </Panel>

      <DiagnosticPanel
        v-if="skillResultDiagnostic"
        :diagnostic="skillResultDiagnostic"
        default-open
      />
    </template>

    <!-- 2. MCP 管理 -->
    <template v-else-if="currentMode === 'mcp'">
      <PageHeader
        index="20"
        title="MCP 管理"
        description="Registry、安装实例、内置社交研究、授权与语义审核保持独立状态；发现工具不会执行工具。"
      >
        <template v-if="currentSection === 'registry'" #actions>
          <div class="search-field">
            <input
              v-model="mcpSearch"
              type="search"
              placeholder="搜索 MCP Registry"
              aria-label="搜索 MCP Registry"
            />
          </div>
        </template>
      </PageHeader>

      <Panel
        :eyebrow="`MCP / ${currentSection.toUpperCase()}`"
        :title="currentSection === 'registry' ? 'Registry' : currentSection === 'installations' ? '安装实例' : currentSection === 'social' ? '内置社交研究' : '授权与语义审核'"
      >
        <template #actions>
          <button
            class="button button-secondary"
            type="button"
            :disabled="mcpReloadMutation.isPending.value"
            @click="handleMcpReload"
          >
            重载 MCP
          </button>
        </template>

        <QueryBoundary
          :pending="mcpQuery.isPending.value"
          :error="mcpQuery.error.value"
          :empty="mcpRows.length === 0"
          empty-text="当前没有对应的 MCP 记录。"
        >
          <div class="table-responsive">
            <table class="data-table">
              <thead>
                <tr>
                  <th scope="col">Server / 平台</th>
                  <th scope="col">说明</th>
                  <th scope="col">运行与授权</th>
                  <th scope="col">工具数</th>
                  <th scope="col">操作</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="(row, idx) in mcpRows" :key="textAt(row, 'installation_id', 'name', 'server_name', 'platform') + idx">
                  <td>
                    <button class="text-link" type="button" @click="selectedMcp = row">
                      <strong>{{ textAt(row, 'name', 'server_name', 'platform', 'service') }}</strong>
                      <br />
                      <code>{{ textAt(row, 'installation_id', 'source_id') }}</code>
                    </button>
                  </td>
                  <td>{{ textAt(row, 'description', 'summary', 'last_error') }}</td>
                  <td>
                    <StateBadge :tone="computeSafeTone(textAt(row, 'status', 'state', 'auth_state', 'process_state'))">
                      {{ textAt(row, 'status', 'state', 'auth_state', 'process_state') }}
                    </StateBadge>
                  </td>
                  <td>{{ textAt(row, 'tool_count', 'enabled_tools', 'tools_count') }}</td>
                  <td>
                    <button
                      v-if="currentSection === 'registry'"
                      class="button button-primary"
                      type="button"
                      :disabled="mcpInstallMutation.isPending.value"
                      @click="handleMcpInstall(row)"
                    >
                      安装
                    </button>
                    <button
                      v-else-if="currentSection === 'installations'"
                      class="button button-secondary"
                      type="button"
                      :disabled="mcpToggleMutation.isPending.value"
                      @click="handleMcpToggle(row)"
                    >
                      {{ row.desired_enabled === true ? '停用' : '启用' }}
                    </button>
                    <span v-else class="muted">使用专用授权流程</span>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </QueryBoundary>
      </Panel>

      <Panel v-if="selectedMcp" eyebrow="MCP / DETAIL" title="实例安全摘要">
        <dl class="detail-list">
          <div>
            <dt>实例 ID</dt>
            <dd><code>{{ textAt(selectedMcp, 'installation_id') }}</code></dd>
          </div>
          <div>
            <dt>来源</dt>
            <dd>{{ textAt(selectedMcp, 'source_id', 'source') }}</dd>
          </div>
          <div>
            <dt>命令类型</dt>
            <dd>{{ textAt(selectedMcp, 'transport', 'command_type') }}</dd>
          </div>
          <div>
            <dt>最后错误</dt>
            <dd>{{ textAt(selectedMcp, 'last_error') }}</dd>
          </div>
        </dl>
      </Panel>

      <DiagnosticPanel
        v-if="mcpResultDiagnostic"
        :diagnostic="mcpResultDiagnostic"
        default-open
      />
    </template>

    <!-- 3. 创建工具 -->
    <template v-else-if="currentMode === 'tool-creator'">
      <PageHeader
        index="21"
        title="创建工具"
        description="任务、管理员问题、事件、产物和验证结果按生命周期展示；副作用级别在批准前必须可见。"
      />

      <Panel
        v-if="currentSection === 'tasks'"
        eyebrow="TOOL CREATOR / NEW"
        title="创建声明式工具任务"
      >
        <div class="stacked-form">
          <label>
            建议名称
            <input v-model="toolSuggestedName" placeholder="例如：weather_query" />
          </label>
          <label>
            需求
            <textarea
              v-model="toolRequest"
              rows="4"
              placeholder="描述工具的功能、输入参数和预期调用方式"
            />
          </label>
          <div>
            <button
              class="button button-primary"
              type="button"
              :disabled="!toolRequest.trim() || toolCreateMutation.isPending.value"
              @click="handleToolCreate"
            >
              创建任务
            </button>
          </div>
        </div>
      </Panel>

      <Panel
        :eyebrow="`TOOL CREATOR / ${currentSection.toUpperCase()}`"
        title="任务列表"
      >
        <QueryBoundary
          :pending="toolTasksQuery.isPending.value"
          :error="toolTasksQuery.error.value"
          :empty="toolTaskRows.length === 0"
          empty-text="当前没有工具创建任务。"
        >
          <div class="table-responsive">
            <table class="data-table">
              <thead>
                <tr>
                  <th scope="col">任务</th>
                  <th scope="col">安全摘要</th>
                  <th scope="col">副作用</th>
                  <th scope="col">阶段</th>
                  <th scope="col">操作</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="(row, idx) in toolTaskRows" :key="textAt(row, 'task_id') + idx">
                  <td>
                    <button class="text-link" type="button" @click="selectedTaskId = textAt(row, 'task_id')">
                      <strong>{{ textAt(row, 'suggested_name') }}</strong>
                      <br />
                      <code>{{ textAt(row, 'task_id') }}</code>
                    </button>
                  </td>
                  <td>{{ textAt(row, 'request_text', 'summary') }}</td>
                  <td>{{ textAt(row, 'side_effect_level', 'risk') }}</td>
                  <td>
                    <StateBadge :tone="computeSafeTone(textAt(row, 'status', 'state', 'phase'))">
                      {{ textAt(row, 'status', 'state', 'phase') }}
                    </StateBadge>
                  </td>
                  <td>
                    <div class="inline-controls">
                      <button
                        class="button button-secondary"
                        type="button"
                        :disabled="toolLifecycleMutation.isPending.value"
                        @click="handleToolLifecycle('retry', row)"
                      >
                        继续 / 重试
                      </button>
                      <button
                        class="button button-danger"
                        type="button"
                        :disabled="toolLifecycleMutation.isPending.value"
                        @click="handleToolLifecycle('cancel', row)"
                      >
                        取消
                      </button>
                    </div>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </QueryBoundary>
      </Panel>

      <Panel
        v-if="selectedTaskId && currentSection !== 'tasks'"
        eyebrow="TOOL CREATOR / DETAIL"
        :title="`任务详情 ${selectedTaskId}`"
      >
        <QueryBoundary
          :pending="toolDetailQuery.isPending.value"
          :error="toolDetailQuery.error.value"
          :empty="toolDetailRows.length === 0"
          empty-text="当前任务没有该类记录。"
        >
          <div class="table-responsive">
            <table class="data-table">
              <thead>
                <tr>
                  <th scope="col">类型</th>
                  <th scope="col">内容 / 产物</th>
                  <th scope="col">验证</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="(row, idx) in toolDetailRows" :key="textAt(row, 'id', 'question_id', 'digest', 'ts') + idx">
                  <td>{{ textAt(row, 'kind', 'type', 'event', 'question') }}</td>
                  <td>{{ textAt(row, 'summary', 'message', 'prompt', 'path') }}</td>
                  <td>
                    <StateBadge :tone="computeSafeTone(textAt(row, 'status', 'state'))">
                      {{ textAt(row, 'status', 'state') }}
                    </StateBadge>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </QueryBoundary>
      </Panel>

      <DiagnosticPanel
        v-if="toolResultDiagnostic"
        :diagnostic="toolResultDiagnostic"
        default-open
      />
    </template>

    <!-- 4. 插件知识库 -->
    <template v-else-if="currentMode === 'plugin-knowledge'">
      <PageHeader
        index="22"
        title="插件知识库"
        description="知识目录、语义搜索、覆盖率和源文件采用业务字段展示；完整源码与未清洗快照只在服务端受控读取。"
      >
        <template #actions>
          <div class="search-field">
            <input
              v-model="knowledgeSearch"
              type="search"
              placeholder="搜索插件名、命令或能力"
              aria-label="搜索插件名、命令或能力"
              @input="knowledgePage = 1"
            />
          </div>
        </template>
      </PageHeader>

      <Panel
        :eyebrow="`PLUGIN KNOWLEDGE / ${currentSection.toUpperCase()}`"
        :title="currentSection === 'search' ? '语义搜索' : currentSection === 'rebuild' ? '索引状态与重建' : '知识目录'"
      >
        <QueryBoundary
          :pending="knowledgeCatalogQuery.isPending.value || knowledgeSearchQuery.isPending.value"
          :error="knowledgeCatalogQuery.error.value || knowledgeSearchQuery.error.value"
          :empty="knowledgeRows.length === 0"
          empty-text="当前没有匹配的插件知识。"
        >
          <div class="table-responsive">
            <table class="data-table">
              <thead>
                <tr>
                  <th scope="col">插件</th>
                  <th scope="col">用途摘要</th>
                  <th scope="col">分类</th>
                  <th scope="col">覆盖率</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="(row, idx) in knowledgeRows" :key="textAt(row, 'plugin_name', 'name') + idx">
                  <td>
                    <button class="text-link" type="button" @click="selectedKnowledgeName = textAt(row, 'plugin_name', 'name')">
                      <strong>{{ textAt(row, 'display_name', 'plugin_name', 'name') }}</strong>
                      <br />
                      <code>{{ textAt(row, 'plugin_name', 'name') }}</code>
                    </button>
                  </td>
                  <td>{{ textAt(row, 'summary', 'description', 'analysis_scope') }}</td>
                  <td>{{ textAt(row, 'category') }}</td>
                  <td>{{ textAt(row, 'coverage', 'source_coverage', 'command_count') }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </QueryBoundary>

        <div
          v-if="currentSection !== 'search' && knowledgeCatalogQuery.data.value && knowledgeCatalogQuery.data.value.total_pages > 1"
          class="pagination"
        >
          <button
            type="button"
            :disabled="knowledgePage <= 1"
            @click="knowledgePage--"
          >
            上一页
          </button>
          <span>第 {{ knowledgeCatalogQuery.data.value.page }} / {{ knowledgeCatalogQuery.data.value.total_pages }} 页</span>
          <button
            type="button"
            :disabled="knowledgePage >= knowledgeCatalogQuery.data.value.total_pages"
            @click="knowledgePage++"
          >
            下一页
          </button>
        </div>
      </Panel>

      <Panel
        v-if="selectedKnowledgeName"
        eyebrow="PLUGIN KNOWLEDGE / DETAIL"
        :title="selectedKnowledgeName"
      >
        <QueryBoundary
          :pending="knowledgeDetailQuery.isPending.value"
          :error="knowledgeDetailQuery.error.value"
        >
          <dl class="detail-list">
            <div>
              <dt>分类</dt>
              <dd>{{ textAt(asRecord(asRecord(knowledgeDetailQuery.data.value).entry), 'category') }}</dd>
            </div>
            <div>
              <dt>摘要</dt>
              <dd>{{ textAt(asRecord(asRecord(knowledgeDetailQuery.data.value).entry), 'summary', 'description') }}</dd>
            </div>
            <div>
              <dt>来源覆盖</dt>
              <dd>{{ textAt(asRecord(knowledgeDetailQuery.data.value), 'source_coverage') }}</dd>
            </div>
            <div>
              <dt>诊断码</dt>
              <dd><code>{{ textAt(asRecord(asRecord(knowledgeDetailQuery.data.value).diagnostic), 'code') }}</code></dd>
            </div>
          </dl>
        </QueryBoundary>
      </Panel>
    </template>

    <!-- 5. 插件管理与更新 -->
    <template v-else-if="currentMode === 'plugins'">
      <PageHeader
        index="23"
        title="插件管理"
        description="命令与 WebUI 共用真实 Git 五源测速、最快源选择、网络失败按排名回退和本地 fast-forward；不会修改 remote 或 Git 配置。"
      />

      <Panel eyebrow="PLUGIN / SOURCE" title="当前版本与仓库">
        <QueryBoundary
          :pending="pluginStatusQuery.isPending.value"
          :error="pluginStatusQuery.error.value"
        >
          <dl v-if="pluginStatusQuery.data.value" class="detail-list">
            <div>
              <dt>分支 / Upstream</dt>
              <dd>{{ pluginStatusQuery.data.value.local?.branch || '—' }} / {{ pluginStatusQuery.data.value.source?.upstream || '—' }}</dd>
            </div>
            <div>
              <dt>本地 HEAD</dt>
              <dd><code>{{ pluginStatusQuery.data.value.local?.short_hash || pluginStatusQuery.data.value.local?.hash || '—' }}</code></dd>
            </div>
            <div>
              <dt>远端 HEAD</dt>
              <dd><code>{{ pluginStatusQuery.data.value.remote?.short_hash || pluginStatusQuery.data.value.remote?.hash || '—' }}</code></dd>
            </div>
            <div>
              <dt>工作区</dt>
              <dd>
                <StateBadge :tone="pluginStatusQuery.data.value.dirty ? 'error' : 'ok'">
                  {{ pluginStatusQuery.data.value.dirty ? `有 ${pluginStatusQuery.data.value.dirty_count} 项修改，拒绝更新` : '干净' }}
                </StateBadge>
              </dd>
            </div>
            <div>
              <dt>更新状态</dt>
              <dd>{{ pluginStatusQuery.data.value.update_available ? `落后 ${pluginStatusQuery.data.value.behind} 个提交` : '已是最新或尚未检查' }}</dd>
            </div>
          </dl>
          <p v-else class="muted">更新服务没有返回状态。</p>
        </QueryBoundary>
      </Panel>

      <template v-if="currentSection === 'update'">
        <Panel
          eyebrow="PLUGIN / BENCHMARK"
          title="四镜像 + 官方源真实 Git 测速"
        >
          <template #actions>
            <div class="inline-controls">
              <button
                class="button button-secondary"
                type="button"
                :disabled="pluginBenchmarkMutation.isPending.value"
                @click="pluginBenchmarkMutation.mutate()"
              >
                重新测速
              </button>
              <button
                class="button button-primary"
                type="button"
                :disabled="pluginCheckMutation.isPending.value"
                @click="pluginCheckMutation.mutate()"
              >
                检查更新
              </button>
            </div>
          </template>

          <div class="table-responsive">
            <table class="data-table">
              <thead>
                <tr>
                  <th scope="col">排名</th>
                  <th scope="col">更新源</th>
                  <th scope="col">延迟</th>
                  <th scope="col">Git 探测</th>
                  <th scope="col">诊断码</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="(probe, idx) in pluginProbes" :key="probe.source_id || idx">
                  <td>{{ probe.rank ? `#${probe.rank}` : '—' }}</td>
                  <td>
                    <strong>{{ probe.display_name }}</strong>
                    <br />
                    <code>{{ probe.kind }}</code>
                  </td>
                  <td>{{ probe.latency_ms == null ? '—' : `${probe.latency_ms} ms` }}</td>
                  <td>
                    <StateBadge :tone="computeSafeTone(probe.state)">{{ probe.state }}</StateBadge>
                  </td>
                  <td><code>{{ probe.diagnostic_code || '—' }}</code></td>
                </tr>
                <tr v-if="pluginProbes.length === 0">
                  <td colspan="5" class="empty-notice">尚未执行五源真实 Git 测速。</td>
                </tr>
              </tbody>
            </table>
          </div>
          <p v-if="activePluginOperation?.selected_source_id" class="muted selected-source-line">
            本次选中源：
            <strong>{{ activeSelectedProbe?.display_name || activePluginOperation.selected_source_id }}</strong>
            ·
            <code>{{ activePluginOperation.selected_source_id }}</code>
          </p>
        </Panel>

        <Panel eyebrow="PLUGIN / APPLY" title="执行更新">
          <p>
            更新前会再次确认工作区干净，并在测速缓存超过 60 秒时重新测速。请输入
            <code>UPDATE</code>；只做 fetch 与本地 <code>merge --ff-only</code>，不会自动重启 Bot。
          </p>
          <div class="stacked-form">
            <input
              v-model="pluginConfirmation"
              aria-label="插件更新确认"
              placeholder="输入 UPDATE 确认执行"
            />
            <div>
              <button
                class="button button-danger"
                type="button"
                :disabled="pluginConfirmation !== 'UPDATE' || pluginApplyMutation.isPending.value || pluginStatusQuery.data.value?.dirty === true"
                @click="pluginApplyMutation.mutate()"
              >
                执行更新
              </button>
            </div>
          </div>
        </Panel>
      </template>

      <Panel
        v-if="currentSection === 'history'"
        eyebrow="PLUGIN / HISTORY"
        title="更新操作历史"
      >
        <QueryBoundary
          :pending="pluginHistoryQuery.isPending.value"
          :error="pluginHistoryQuery.error.value"
          :empty="pluginHistoryRows.length === 0"
          empty-text="当前进程没有更新操作历史。"
        >
          <div class="table-responsive">
            <table class="data-table">
              <thead>
                <tr>
                  <th scope="col">开始时间</th>
                  <th scope="col">Operation</th>
                  <th scope="col">结果</th>
                  <th scope="col">实际源</th>
                  <th scope="col">诊断码</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="(row, idx) in pluginHistoryRows" :key="textAt(row, 'operation_id') + idx">
                  <td>{{ formatDateTime(row.started_at) }}</td>
                  <td><code>{{ textAt(row, 'operation_id') }}</code></td>
                  <td>
                    <StateBadge :tone="computeSafeTone(textAt(row, 'state'))">
                      {{ textAt(row, 'state') }}
                    </StateBadge>
                  </td>
                  <td>{{ textAt(row, 'selected_source_id') }}</td>
                  <td><code>{{ textAt(row, 'diagnostic_code') }}</code></td>
                </tr>
              </tbody>
            </table>
          </div>
        </QueryBoundary>
      </Panel>

      <DiagnosticPanel
        v-if="pluginResultDiagnostic"
        :diagnostic="pluginResultDiagnostic"
        default-open
      />
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from "vue";
import { useMutation, useQuery, useQueryClient } from "@tanstack/vue-query";
import { useRoute } from "vue-router";

import { safeDiagnostic } from "@/api/diagnostics";
import { resources } from "@/api/resources";
import type { CatalogItem, OperationDiagnostic, Page, PluginUpdateOperation, PluginUpdateStatus } from "@/api/types";
import { formatDateTime } from "@/lib/format";
import DiagnosticPanel from "@vue-app/components/DiagnosticPanel.vue";
import PageHeader from "@vue-app/components/PageHeader.vue";
import Panel from "@vue-app/components/Panel.vue";
import QueryBoundary from "@vue-app/components/QueryBoundary.vue";
import StateBadge from "@vue-app/components/StateBadge.vue";

type BusinessRecord = Record<string, unknown>;
type Mode = "skills" | "mcp" | "tool-creator" | "plugin-knowledge" | "plugins";

const props = withDefaults(
  defineProps<{
    mode?: Mode;
  }>(),
  {
    mode: undefined,
  },
);

const route = useRoute();
const client = useQueryClient();

const currentMode = computed<Mode>(() => {
  if (props.mode) return props.mode;
  const metaMode = route.meta?.mode as Mode | undefined;
  if (metaMode) return metaMode;
  const path = route.path;
  if (path.includes("/capability/skills")) return "skills";
  if (path.includes("/capability/mcp")) return "mcp";
  if (path.includes("/capability/tool-creator")) return "tool-creator";
  if (path.includes("/capability/plugin-knowledge")) return "plugin-knowledge";
  if (path.includes("/capability/plugins")) return "plugins";
  return "skills";
});

const currentSection = computed<string>(() => {
  return String(route.params.section || (
    currentMode.value === "skills" ? "installed" :
    currentMode.value === "mcp" ? "registry" :
    currentMode.value === "tool-creator" ? "tasks" :
    currentMode.value === "plugin-knowledge" ? "catalog" :
    "status"
  ));
});

function asRecord(value: unknown): BusinessRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as BusinessRecord)
    : {};
}

function textAt(row: unknown, ...keys: string[]): string {
  const record = asRecord(row);
  if (Object.keys(record).length === 0) return "—";
  for (const key of keys) {
    const val = record[key];
    if (val !== undefined && val !== null && String(val).trim() !== "") {
      return String(val);
    }
  }
  return "—";
}

function recordsAt(data: unknown, ...keys: string[]): BusinessRecord[] {
  if (!data) return [];
  const rec = asRecord(data);
  for (const key of keys) {
    const val = rec[key];
    if (Array.isArray(val)) {
      return val.filter((item): item is BusinessRecord => typeof item === "object" && item !== null);
    }
  }
  if (Array.isArray(data)) {
    return data.filter((item): item is BusinessRecord => typeof item === "object" && item !== null);
  }
  return [];
}

function computeSafeTone(value: unknown): "ok" | "warn" | "error" | "running" | "unknown" {
  const val = String(value || "").toLowerCase();
  if (["ready", "succeeded", "ok", "healthy", "enabled", "active", "online", "verified"].includes(val)) return "ok";
  if (["running", "probing", "fetching", "applying", "pending"].includes(val)) return "running";
  if (["warn", "timeout", "quarantined", "unverified", "stale"].includes(val)) return "warn";
  if (["failed", "error", "disabled", "rejected", "inapplicable", "definite_failure"].includes(val)) return "error";
  return "unknown";
}

function parseDiagnostic(value: unknown): OperationDiagnostic | null {
  if (!value) return null;
  const row = asRecord(value);
  const diagnostic = asRecord(row.diagnostic);
  if (Object.keys(diagnostic).length) return safeDiagnostic(diagnostic);

  const operation = asRecord(row.operation);
  const code = textAt(row, "code", "diagnostic_code") !== "—"
    ? textAt(row, "code", "diagnostic_code")
    : textAt(operation, "diagnostic_code");
  if (code === "—") return null;

  const state = textAt(operation, "state");
  const ok = row.ok === true || ["ready", "succeeded"].includes(state);
  return safeDiagnostic({
    ok,
    code,
    phase: textAt(row, "phase") === "—" ? "operation_complete" : textAt(row, "phase"),
    message: textAt(row, "message", "error") === "—"
      ? ok ? "服务端已确认操作结果。" : "操作未完成，请依据诊断码核对。"
      : textAt(row, "message", "error"),
    operation_id: textAt(operation, "operation_id") === "—" ? undefined : textAt(operation, "operation_id"),
    retryable: false,
    partial: false,
    outcome_unknown: state === "unknown",
    warnings: [],
    steps: [],
  });
}

// ----------------------------------------------------
// 1. Skills Logic
// ----------------------------------------------------
const skillPage = ref(1);
const skillSearch = ref("");

const skillsQuery = useQuery<Page<CatalogItem>>({
  queryKey: computed(() => ["skills", skillPage.value, skillSearch.value]),
  queryFn: ({ signal }) => resources.catalog("skills", skillPage.value, 20, skillSearch.value, signal),
  enabled: computed(() => currentMode.value === "skills"),
});

const skillToggleMutation = useMutation({
  mutationFn: ({ name, disabled }: { name: string; disabled: boolean }) =>
    resources.skillAction(`${encodeURIComponent(name)}/toggle`, { disabled, reason: "webui_explicit_toggle" }),
  onSuccess: () => void client.invalidateQueries({ queryKey: ["skills"] }),
});

const skillReloadMutation = useMutation({
  mutationFn: () => resources.skillAction("reload"),
});

const skillRemoteToggleMutation = useMutation({
  mutationFn: (enabled: boolean) => resources.skillAction("remote/toggle", { enabled }),
});

const skillRows = computed(() => skillsQuery.data.value?.items ?? []);

function handleRemoteToggle(enabled: boolean): void {
  if (enabled) {
    if (window.confirm("确认开启远程 Skill 加载？仍需单独审核来源。")) {
      skillRemoteToggleMutation.mutate(true);
    }
  } else {
    skillRemoteToggleMutation.mutate(false);
  }
}

function handleSkillReload(): void {
  if (window.confirm("确认重载 Skill runtime？")) {
    skillReloadMutation.mutate();
  }
}

function handleSkillToggle(row: BusinessRecord): void {
  const disabled = row.user_disabled === true;
  const name = textAt(row, "name");
  if (window.confirm(`确认${disabled ? "启用" : "禁用"} Skill ${name}？`)) {
    skillToggleMutation.mutate({ name, disabled: !disabled });
  }
}

const skillResultDiagnostic = computed(() =>
  parseDiagnostic(skillToggleMutation.data.value ?? skillReloadMutation.data.value ?? skillRemoteToggleMutation.data.value)
);

// ----------------------------------------------------
// 2. MCP Management Logic
// ----------------------------------------------------
const mcpSearch = ref("");
const selectedMcp = ref<BusinessRecord | null>(null);

const mcpEndpoint = computed(() => {
  if (currentSection.value === "installations") return "installations";
  if (currentSection.value === "social" || currentSection.value === "review") return "builtin/social-research/status";
  return "search";
});

const mcpQuery = useQuery({
  queryKey: computed(() => ["mcp-management", mcpEndpoint.value, mcpSearch.value]),
  queryFn: ({ signal }) =>
    mcpEndpoint.value === "search"
      ? resources.mcpGet(mcpEndpoint.value, { q: mcpSearch.value, source_id: "official", limit: 20 }, signal)
      : resources.mcpGet(mcpEndpoint.value, {}, signal),
  enabled: computed(() => currentMode.value === "mcp"),
});

const mcpReloadMutation = useMutation({
  mutationFn: () => resources.mcpPost("reload"),
  onSuccess: () => void client.invalidateQueries({ queryKey: ["mcp-management"] }),
});

const mcpToggleMutation = useMutation({
  mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) =>
    resources.mcpPost(`installations/${encodeURIComponent(id)}/toggle`, { enabled }),
  onSuccess: () => void client.invalidateQueries({ queryKey: ["mcp-management"] }),
});

const mcpInstallMutation = useMutation({
  mutationFn: (row: BusinessRecord) =>
    resources.mcpPost("install", {
      source_id: textAt(row, "source_id") === "—" ? "official" : textAt(row, "source_id"),
      name: textAt(row, "name", "server_name"),
      confirm_execution: true,
    }),
  onSuccess: () => void client.invalidateQueries({ queryKey: ["mcp-management"] }),
});

const mcpRows = computed<BusinessRecord[]>(() => {
  if (currentSection.value === "installations") {
    return recordsAt(mcpQuery.data.value, "installations");
  }
  if (currentSection.value === "registry") {
    return recordsAt(mcpQuery.data.value, "servers", "items", "results");
  }
  const single = asRecord(mcpQuery.data.value);
  return Object.keys(single).length > 0 ? [single] : [];
});

function handleMcpReload(): void {
  if (window.confirm("确认重载 MCP process 与工具目录？")) {
    mcpReloadMutation.mutate();
  }
}

function handleMcpInstall(row: BusinessRecord): void {
  const name = textAt(row, "name", "server_name");
  if (window.confirm(`确认安装 MCP ${name}？安装可能启动本地进程。`)) {
    mcpInstallMutation.mutate(row);
  }
}

function handleMcpToggle(row: BusinessRecord): void {
  const id = textAt(row, "installation_id");
  const desired = row.desired_enabled !== true;
  mcpToggleMutation.mutate({ id, enabled: desired });
}

const mcpResultDiagnostic = computed(() =>
  parseDiagnostic(mcpReloadMutation.data.value ?? mcpToggleMutation.data.value ?? mcpInstallMutation.data.value)
);

// ----------------------------------------------------
// 3. Tool Creator Logic
// ----------------------------------------------------
const toolRequest = ref("");
const toolSuggestedName = ref("");
const selectedTaskId = ref("");

const toolTasksQuery = useQuery({
  queryKey: ["tool-creator-tasks"],
  queryFn: ({ signal }) => resources.toolCreatorGet("tasks", signal),
  enabled: computed(() => currentMode.value === "tool-creator"),
});

const toolDetailQuery = useQuery({
  queryKey: computed(() => ["tool-creator-detail", selectedTaskId.value]),
  queryFn: ({ signal }) => resources.toolCreatorGet(`tasks/${encodeURIComponent(selectedTaskId.value)}`, signal),
  enabled: computed(() => currentMode.value === "tool-creator" && Boolean(selectedTaskId.value) && currentSection.value !== "tasks"),
});

const toolCreateMutation = useMutation({
  mutationFn: () => resources.toolCreatorPost("tasks", { request: toolRequest.value, suggested_name: toolSuggestedName.value }),
  onSuccess: () => {
    toolRequest.value = "";
    toolSuggestedName.value = "";
    void client.invalidateQueries({ queryKey: ["tool-creator-tasks"] });
  },
});

const toolLifecycleMutation = useMutation({
  mutationFn: ({ action, task }: { action: "cancel" | "retry"; task: BusinessRecord }) =>
    resources.toolCreatorPost(`tasks/${encodeURIComponent(textAt(task, "task_id"))}/${action}`, {
      expected_version: Number(task.version ?? 0),
    }),
  onSuccess: () => void client.invalidateQueries({ queryKey: ["tool-creator-tasks"] }),
});

const toolTaskRows = computed(() => recordsAt(toolTasksQuery.data.value, "tasks"));
const toolDetailRows = computed(() => recordsAt(toolDetailQuery.data.value, "events", "questions", "artifacts"));

function handleToolCreate(): void {
  if (window.confirm("确认创建工具生成任务？产物仍需验证和批准后才发布。")) {
    toolCreateMutation.mutate();
  }
}

function handleToolLifecycle(action: "cancel" | "retry", task: BusinessRecord): void {
  const taskId = textAt(task, "task_id");
  if (action === "cancel") {
    if (window.confirm(`确认取消任务 ${taskId}？`)) {
      toolLifecycleMutation.mutate({ action, task });
    }
  } else {
    toolLifecycleMutation.mutate({ action, task });
  }
}

const toolResultDiagnostic = computed(() =>
  parseDiagnostic(toolCreateMutation.data.value ?? toolLifecycleMutation.data.value)
);

// ----------------------------------------------------
// 4. Plugin Knowledge Logic
// ----------------------------------------------------
const knowledgePage = ref(1);
const knowledgeSearch = ref("");
const selectedKnowledgeName = ref("");

const knowledgeCatalogQuery = useQuery<Page<CatalogItem>>({
  queryKey: computed(() => ["plugin-knowledge", knowledgePage.value, knowledgeSearch.value]),
  queryFn: ({ signal }) => resources.catalog("plugin-knowledge", knowledgePage.value, 20, knowledgeSearch.value, signal),
  enabled: computed(() => currentMode.value === "plugin-knowledge" && (currentSection.value !== "search" || !knowledgeSearch.value.trim())),
});

const knowledgeSearchQuery = useQuery({
  queryKey: computed(() => ["plugin-knowledge-search", knowledgeSearch.value]),
  queryFn: ({ signal }) => resources.pluginKnowledgeSearch(knowledgeSearch.value, signal),
  enabled: computed(() => currentMode.value === "plugin-knowledge" && currentSection.value === "search" && Boolean(knowledgeSearch.value.trim())),
});

const knowledgeDetailQuery = useQuery({
  queryKey: computed(() => ["plugin-knowledge-detail", selectedKnowledgeName.value]),
  queryFn: ({ signal }) => resources.pluginKnowledgeDetail(selectedKnowledgeName.value, signal),
  enabled: computed(() => currentMode.value === "plugin-knowledge" && Boolean(selectedKnowledgeName.value)),
});

const knowledgeRows = computed<BusinessRecord[]>(() => {
  if (currentSection.value === "search") {
    return recordsAt(knowledgeSearchQuery.data.value, "results", "items");
  }
  return knowledgeCatalogQuery.data.value?.items ?? [];
});

// ----------------------------------------------------
// 5. Plugin Management Logic
// ----------------------------------------------------
const pluginConfirmation = ref("");

const pluginStatusQuery = useQuery<PluginUpdateStatus>({
  queryKey: ["plugin-update-status"],
  queryFn: ({ signal }) => resources.pluginUpdateStatus(signal),
  refetchInterval: false,
  enabled: computed(() => currentMode.value === "plugins"),
});

const pluginHistoryQuery = useQuery({
  queryKey: ["plugin-update-history"],
  queryFn: ({ signal }) => resources.pluginUpdateHistory(signal),
  enabled: computed(() => currentMode.value === "plugins" && currentSection.value === "history"),
});

const pluginBenchmarkMutation = useMutation({
  mutationFn: () => resources.pluginUpdateBenchmark(),
  onSuccess: () => void client.invalidateQueries({ queryKey: ["plugin-update-status"] }),
});

const pluginCheckMutation = useMutation({
  mutationFn: () => resources.pluginUpdateCheck(),
  onSuccess: () => void client.invalidateQueries({ queryKey: ["plugin-update-status"] }),
});

const pluginApplyMutation = useMutation({
  mutationFn: () => resources.pluginUpdateApply(),
  onSuccess: () => {
    pluginConfirmation.value = "";
    void client.invalidateQueries({ queryKey: ["plugin-update-status"] });
  },
});

const activePluginOperation = computed<PluginUpdateOperation | undefined>(() => {
  return (
    pluginApplyMutation.data.value?.operation ??
    pluginCheckMutation.data.value?.operation ??
    pluginBenchmarkMutation.data.value?.operation ??
    pluginStatusQuery.data.value?.operation
  );
});

const pluginProbes = computed(() => activePluginOperation.value?.probes ?? []);
const activeSelectedProbe = computed(() =>
  pluginProbes.value.find((probe) => probe.source_id === activePluginOperation.value?.selected_source_id)
);

const pluginHistoryRows = computed<PluginUpdateOperation[]>(() =>
  pluginHistoryQuery.data.value?.items ?? []
);

const pluginResultDiagnostic = computed(() =>
  parseDiagnostic(pluginApplyMutation.data.value ?? pluginCheckMutation.data.value ?? pluginBenchmarkMutation.data.value)
);
</script>
