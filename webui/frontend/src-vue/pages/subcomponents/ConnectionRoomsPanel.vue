<template>
  <details @toggle="opened"><summary>房间、Topic 与群管理</summary>
    <p v-if="feedback" role="status">{{ feedback }}</p>
    <p>默认仅观察参与判断，不自动插话。群管理权限尚未验证，执行前会重新核验。</p>
    <p v-if="loaded && !rooms.length">尚无房间记录。请先在目标群或 Topic 发送一条消息，刷新连接后再展开设置。</p>
    <div class="upgrade-actions"><button v-for="room in rooms" :key="room.scope_key" class="button button-quiet" type="button" @click="select(room)">{{ room.title || room.chat_id }}{{ room.topic_id ? ` · Topic ${room.topic_id}` : '' }} · {{ modeLabels[room.participation] }}</button></div>
    <form class="upgrade-form" @submit.prevent="save">
      <label>Chat ID<input v-model="draft.chat_id" required pattern="-?[0-9]+" /></label>
      <label>Topic ID（可选）<input v-model="draft.topic_id" pattern="[0-9]*" /></label>
      <label>参与方式<select v-model="draft.participation"><option value="off">关闭自主参与</option><option value="shadow">仅观察判断</option><option value="on">开启自主参与</option></select></label>
      <label class="upgrade-wide"><span><input v-model="draft.proactive_enabled" type="checkbox" style="width:auto" :disabled="!draft.chat_id.startsWith('-')" /> 允许此群 / Topic 的主动消息</span></label>
      <p>需要同时在配置中开启插件、主动消息和群聊空闲发话；默认不主动外发。私聊不自动主动联系。</p>
      <button class="button button-quiet" :disabled="busy">保存房间设置</button>
    </form>
    <form class="upgrade-form" @submit.prevent="prepare">
      <label>管理动作<select v-model="action"><option value="mute">禁言</option><option value="unmute">解除禁言</option><option value="kick">移出群聊</option><option value="delete">撤回消息</option><option value="pin">置顶消息</option></select></label>
      <label v-if="['mute','unmute','kick'].includes(action)">目标用户 ID<input v-model="target" required pattern="[0-9]+" /></label>
      <label v-else>消息 ID<input v-model="message" required pattern="[0-9]+" /></label>
      <label v-if="action === 'mute'">禁言分钟数<input v-model.number="minutes" type="number" min="1" max="527040" required /></label>
      <button class="button button-quiet" :disabled="busy || !draft.chat_id">预览管理动作</button>
    </form>
    <div v-if="pending" class="upgrade-card"><p>确认{{ actionLabels[pending.action] }}？群 {{ pending.chat_id }}，{{ pending.target_id ? `用户 ${pending.target_id}` : `消息 ${pending.message_id}` }}{{ pending.action === 'mute' ? `，${pending.minutes} 分钟` : '' }}。此操作会在 Telegram 真实执行。</p><div class="upgrade-actions"><button class="button button-danger" :disabled="busy" @click="execute">确认执行</button><button class="button button-quiet" @click="pending = undefined">取消</button></div></div>
  </details>
</template>
<script setup lang="ts">
import { reactive, ref } from 'vue';
import { upgrades, type TelegramRoom } from '@/api/upgrades';
const props = defineProps<{connectionId: string}>();
const rooms = ref<TelegramRoom[]>([]), loaded = ref(false), busy = ref(false), feedback = ref('');
const draft = reactive({chat_id:'',topic_id:'',participation:'shadow',proactive_enabled:false});
const action = ref('mute'), target = ref(''), message = ref(''), minutes = ref(10);
const pending = ref<{action:string;chat_id:string;target_id:string;message_id:string;minutes:number}>();
const modeLabels = {off:'关闭',shadow:'仅观察',on:'已开启'};
const actionLabels: Record<string,string> = {mute:'禁言',unmute:'解除禁言',kick:'移出群聊',delete:'撤回消息',pin:'置顶消息'};
async function load() { try { rooms.value = (await upgrades.rooms(props.connectionId)).items; loaded.value = true; } catch { feedback.value = '房间读取失败，请重试。'; } }
function opened(event: Event) { if ((event.target as HTMLDetailsElement).open && !loaded.value) void load(); }
function select(room: TelegramRoom) { Object.assign(draft,{chat_id:room.chat_id,topic_id:room.topic_id,participation:room.participation,proactive_enabled:room.proactive_enabled}); pending.value = undefined; }
async function save() { busy.value = true; try { await upgrades.saveRoom(props.connectionId,{...draft,proactive_enabled:draft.chat_id.startsWith('-') && draft.proactive_enabled}); await load(); feedback.value = '房间参与设置已保存。'; } catch { feedback.value = '保存失败，请检查房间编号和连接。'; } finally { busy.value = false; } }
function prepare() { if (!/^-?[0-9]+$/.test(draft.chat_id)) { feedback.value = '请先填写有效 Chat ID。'; return; } pending.value = {action:action.value,chat_id:draft.chat_id,target_id:['mute','unmute','kick'].includes(action.value) ? target.value : '',message_id:['delete','pin'].includes(action.value) ? message.value : '',minutes:minutes.value}; }
async function execute() { if (!pending.value) return; const value = {...pending.value}; pending.value = undefined; busy.value = true; try { const result = await upgrades.moderate(props.connectionId,{action:value.action,chat_id:value.chat_id,target_id:value.target_id,message_id:value.message_id,...(value.action === 'mute' ? {until_date:Math.floor(Date.now()/1000)+value.minutes*60} : {})}); feedback.value = result.state === 'sent' ? '管理动作已确认完成。' : result.state === 'unknown' ? '执行结果未知，请在 Telegram 核实；不要直接重复执行。' : '管理动作未执行，请检查当前权限和目标状态。'; } catch { feedback.value = '未确认执行结果，请先在 Telegram 核实再决定后续操作。'; } finally { busy.value = false; } }
</script>
