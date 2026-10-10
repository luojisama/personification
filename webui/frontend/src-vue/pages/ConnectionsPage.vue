<template>
  <div class="page-stack upgrade-page">
    <PageHeader index="角色体验" title="Telegram 接入" description="接入官方 Telegram 适配器，分别确认凭据、收发与群消息可见性。" />
    <p v-if="feedback" role="status">{{ feedback }}</p>
    <Panel title="新增网页托管 Bot">
      <form class="upgrade-form" @submit.prevent="save">
        <label>连接名称<input v-model="draft.name" autocomplete="off" /></label>
        <label>Bot Token<input v-model="draft.token" type="password" autocomplete="new-password" required @input="probed = false" /></label>
        <label>代理地址（可选）<input v-model="draft.proxy" placeholder="http://127.0.0.1:7890" @input="probed = false" /></label>
        <label>Bot API 地址<input v-model="draft.api_server" placeholder="https://api.telegram.org" @input="probed = false" /></label>
        <label>人格说明<textarea v-model="draft.persona" placeholder="留空使用当前默认人格；或填写此 Bot 的人格说明" /></label>
        <label>使用范围<select v-model="draft.scope"><option value="private">仅私聊</option><option value="all">私聊与群聊</option></select></label>
        <p class="muted-copy">凭据只发送到服务端，离开本页即清除输入。群聊是否可见取决于 Privacy Mode、Bot 身份和群权限；新群主动参与保持关闭。</p>
        <div class="upgrade-actions"><button class="button button-quiet" type="button" :disabled="busy || !draft.token" @click="probe">{{ busy ? '处理中…' : '1. 检查连接' }}</button><button class="button" :disabled="busy || !probed">2. 保存连接</button></div>
      </form>
      <dl v-if="probeStatus" class="upgrade-facts"><template v-for="(value, key) in probeStatus" :key="key"><dt>{{ checkLabel(String(key)) }}</dt><dd>{{ checkText(value) }}</dd></template></dl>
    </Panel>
    <Panel title="已接入 Bot"><template #actions><button class="button button-quiet" :disabled="busy" @click="load">刷新</button></template>
      <p v-if="loading">正在读取连接…</p><p v-else-if="!items.length">还没有连接。也可在 NoneBot 配置官方 Telegram 适配器，连接后会自动显示。</p>
      <article v-for="item in items" :key="item.id" class="upgrade-card">
        <h3>{{ item.name || item.username || item.bot_id }}</h3><p>{{ item.source === 'native' ? '由 NoneBot 配置管理' : '网页托管' }} · {{ stateLabel(item.state) }}</p>
        <dl class="upgrade-facts"><template v-for="key in checks" :key="key"><dt>{{ checkLabel(key) }}</dt><dd>{{ checkText(item.status?.[key]) }}</dd></template></dl>
        <p v-if="item.last_error">{{ errorLabel(item.last_error) }}</p><button v-if="item.source === 'managed' && item.last_error === 'webhook_conflict'" class="button button-danger" :disabled="busy" @click="migration = item">迁移现有 Webhook</button>
        <div class="upgrade-actions"><template v-if="item.source === 'managed'"><button class="button" :disabled="busy" @click="action(item, 'start')">启用</button><button class="button button-quiet" :disabled="busy" @click="action(item, 'stop')">停止</button><button v-if="item.state !== 'running'" class="button button-quiet" :disabled="busy" @click="edit(item)">编辑</button><button v-if="item.state !== 'running'" class="button button-danger" :disabled="busy" @click="deleting = item">删除</button></template><button class="button button-quiet" :disabled="busy" @click="action(item, 'check')">检查状态</button><a v-if="safeTestUrl(item.test_url)" class="button button-quiet" :href="safeTestUrl(item.test_url)" target="_blank" rel="noopener noreferrer">打开 TG 完成首条对话</a></div>
        <ConnectionRoomsPanel :connection-id="item.id" />
      </article>
    </Panel>
    <Panel v-if="editing" title="编辑停止的连接"><form class="upgrade-form" @submit.prevent="update"><label>名称<input v-model="editDraft.name" /></label><label>使用范围<select v-model="editDraft.scope"><option value="private">仅私聊</option><option value="all">私聊与群聊</option></select></label><label class="upgrade-wide">人格说明<textarea v-model="editDraft.persona" /></label><div class="upgrade-actions"><button class="button" :disabled="busy">保存修改</button><button class="button button-quiet" type="button" @click="editing = undefined">取消</button></div></form></Panel>
    <Panel v-if="migration" title="迁移接收方式"><p>将删除此 Bot 的现有 Webhook，切换到本服务长轮询。旧服务将不再接收消息，待处理更新会保留。</p><div class="upgrade-actions"><button class="button button-danger" :disabled="busy" @click="migrate">确认迁移并启用</button><button class="button button-quiet" @click="migration = undefined">取消</button></div></Panel>
    <Panel v-if="deleting" title="删除托管连接"><p>删除 {{ deleting.name || deleting.bot_id }} 的本地连接及凭据？之后需要重新填写 Token 接入。</p><div class="upgrade-actions"><button class="button button-danger" :disabled="busy" @click="remove">确认删除</button><button class="button button-quiet" @click="deleting = undefined">取消</button></div></Panel>
  </div>
</template>
<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue';
import { upgrades, type Connection } from '@/api/upgrades';
import { ApiError } from '@/api/client';
import PageHeader from '@vue-app/components/PageHeader.vue';
import Panel from '@vue-app/components/Panel.vue';
import ConnectionRoomsPanel from '@vue-app/pages/subcomponents/ConnectionRoomsPanel.vue';
import './upgradePages.css';
const draft = reactive({token:'', name:'', proxy:'', api_server:'https://api.telegram.org', persona:'', scope:'private'});
const items = ref<Connection[]>([]), loading = ref(true), busy = ref(false), probed = ref(false), feedback = ref('');
const probeStatus = ref<Record<string, unknown>>();
const migration = ref<Connection>(), deleting = ref<Connection>(), editing = ref<Connection>();
const editDraft = reactive({name:'',persona:'',scope:'private'});
const checks = ['credentials','reception','delivery','visibility','permissions'];
const checkLabel = (key: string) => ({credentials:'凭据有效',reception:'接收正常',delivery:'发送确认',visibility:'群消息可见性',permissions:'群权限'}[key] || key);
const checkText = (value: unknown): string => { if (value && typeof value === 'object') return checkText((value as Record<string,unknown>).status); return ({verified:'已验证',ok:'已验证',true:'已验证',failed:'未通过',false:'未通过',unknown:'未验证',unverified:'未验证'}[String(value)] || '未验证'); };
const stateLabel = (value?: string) => ({running:'运行中',stopped:'已停止',error:'连接异常',starting:'连接中',connected:'已连接'}[value || ''] || '未验证');
function safeTestUrl(value?: string) { try { const url = new URL(value || ''); return url.protocol === 'https:' && url.hostname === 't.me' ? url.href : undefined; } catch { return undefined; } }
async function load() { loading.value = true; try { items.value = (await upgrades.connections()).items; } catch { feedback.value = '连接列表读取失败，请检查管理服务后重试。'; } finally { loading.value = false; } }
async function probe() { busy.value = true; probed.value = false; try { const snapshot = JSON.stringify(draft); const result = await upgrades.probe({...draft}); if (snapshot !== JSON.stringify(draft)) { feedback.value = '输入已变更，请重新检查连接。'; return; } probeStatus.value = result.connection?.status || result.status; probed.value = true; feedback.value = result.connection?.webhook_conflict ? '凭据有效，但已有 Webhook。保存后需要明确确认迁移，才能启用长轮询。' : '检查已完成。保存后启用连接，再到 TG 验证实际收发。'; } catch (error) { feedback.value = error instanceof ApiError ? errorLabel(error.code) : '连接检查未通过，请核对 Token、代理和官方适配器依赖。'; } finally { busy.value = false; } }
async function save() { busy.value = true; try { await upgrades.saveConnection({...draft}); draft.token = ''; probed.value = false; feedback.value = '连接已保存，请启用并完成 TG 对话验证。'; await load(); } catch { feedback.value = '保存失败，请检查服务状态或是否已存在相同 Bot。'; } finally { busy.value = false; } }
async function action(item: Connection, name: string, body?: unknown) { busy.value = true; try { await upgrades.connectionAction(item.id, name, body); await load(); feedback.value = '连接状态已更新。'; } catch { await load(); feedback.value = '操作未完成，请检查下方连接诊断。'; } finally { busy.value = false; } }
const errorLabel = (code: string) => ({webhook_conflict:'已有 Webhook 接收消息，请先确认是否迁移。',receiver_conflict:'同一 Bot 已由其他连接接收，请先停用原连接。',http_driver_missing:'缺少 HTTP 客户端驱动，请安装 nonebot2[httpx] 并在驱动配置中启用 ~httpx。',adapter_missing:'请安装 nonebot-adapter-telegram==0.1.0b20 后重启 NoneBot。',invalid_token:'Token 无效，请在 BotFather 检查凭据。'}[code] || '连接存在异常，请检查网络、凭据与适配器依赖。');
function edit(item: Connection) { editing.value = item; Object.assign(editDraft,{name:item.name || '',persona:item.persona || '',scope:item.scope || 'private'}); }
async function update() { if (!editing.value) return; busy.value = true; try { await upgrades.updateConnection(editing.value.id,{...editDraft}); editing.value = undefined; await load(); feedback.value = '连接设置已更新。'; } catch { feedback.value = '更新失败，请先停止连接并检查服务状态。'; } finally { busy.value = false; } }
async function migrate() { if (!migration.value) return; await action(migration.value,'start',{migrate_webhook:true}); migration.value = undefined; }
async function remove() { if (!deleting.value) return; busy.value = true; try { await upgrades.deleteConnection(deleting.value.id); deleting.value = undefined; await load(); feedback.value = '本地连接已删除。'; } catch { feedback.value = '删除未完成，请先停止连接再重试。'; } finally { busy.value = false; } }
onMounted(load);
</script>
