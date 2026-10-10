<template>
<div class="page-stack upgrade-page"><PageHeader index="角色体验" title="共同经历" description="按平台、Bot 和话题查看经历。修订和遗忘会同步使旧摘要与索引失效。" />
<Panel title="选择会话"><form class="upgrade-form" @submit.prevent="load"><label>平台<select v-model="scope.platform"><option value="telegram">Telegram</option><option value="onebot">OneBot</option><option value="satori">Satori</option></select></label><label>Bot ID<input v-model="scope.bot_id" required /></label><label>群 / Chat ID<input v-model="scope.group_id" /></label><label>私聊用户 ID<input v-model="scope.user_id" /></label><label>Topic ID（可选）<input v-model="scope.topic_id" /></label><button class="button" :disabled="busy || !scope.bot_id || (!scope.group_id && !scope.user_id)">查询经历</button></form></Panel>
<p v-if="feedback" role="status">{{ feedback }}</p>
<Panel title="经历卡"><p v-if="loaded && !enabled">经历整理当前已关闭，仍可查看与遗忘已有记录。开启后才能修订。</p><p v-if="!items.length">{{ loaded ? '当前会话还没有经历卡。' : '请选择准确的会话范围后查询。' }}</p>
<article v-for="item in items" :key="item.memory_id" class="upgrade-card"><h3>{{ item.kind === 'stable_identity' ? '稳定身份资料' : '共同经历' }} · 修订 {{ item.revision }}</h3><p>{{ item.confirmation === 'confirmed' ? '已确认' : '待核验，不作为可信事实' }}</p><p>{{ item.summary }}</p><p>参与者：{{ item.participants.join('、') || '未记录' }}</p><details><summary>来源消息</summary><ul><li v-for="source in item.source_refs" :key="source.message_id">{{ source.message_id }} · {{ source.user_id }} · {{ source.timestamp }}</li></ul></details><div class="upgrade-actions"><button class="button button-quiet" :disabled="busy || !enabled" @click="edit(item)">修订</button><button class="button button-danger" :disabled="busy" @click="forgetTarget = item">遗忘</button></div></article>
</Panel>
<Panel v-if="editing" title="修订经历"><form class="upgrade-form" @submit.prevent="revise"><label class="upgrade-wide">准确的经历内容<textarea v-model="summary" required /></label><label>核验状态<select v-model="confirmation"><option value="pending">待核验</option><option value="confirmed">已人工核验来源</option></select></label><label v-if="editing.kind === 'stable_identity'">有效期截止<input v-model="expiresAt" type="datetime-local" :required="confirmation === 'confirmed'" /></label><div class="upgrade-actions"><button class="button" :disabled="busy">保存修订</button><button class="button button-quiet" type="button" @click="editing = undefined">取消</button></div></form></Panel>
<Panel v-if="forgetTarget" title="确认遗忘"><p>遗忘这条经历及其派生资料？此操作不能通过本页面撤销。</p><p>{{ forgetTarget.summary }}</p><div class="upgrade-actions"><button class="button button-danger" :disabled="busy" @click="forget">确认遗忘</button><button class="button button-quiet" @click="forgetTarget = undefined">取消</button></div></Panel>
</div>
</template>
<script setup lang="ts">
import { reactive, ref } from 'vue';
import { upgrades, type Experience, type ExperienceScope } from '@/api/upgrades';
import PageHeader from '@vue-app/components/PageHeader.vue';
import Panel from '@vue-app/components/Panel.vue';
import './upgradePages.css';
const scope = reactive<ExperienceScope>({platform:'telegram',bot_id:'',group_id:'',user_id:'',topic_id:''});
const loadedScope = ref<ExperienceScope>({...scope});
const items = ref<Experience[]>([]), busy = ref(false), loaded = ref(false), enabled = ref(true), feedback = ref('');
const editing = ref<Experience>(), forgetTarget = ref<Experience>(), summary = ref(''), confirmation = ref('pending'), expiresAt = ref('');
async function load() { busy.value = true; editing.value = undefined; forgetTarget.value = undefined; try { const snapshot = {...scope}; const response = await upgrades.experiences(snapshot); items.value = response.items; enabled.value = response.enabled !== false; loadedScope.value = snapshot; loaded.value = true; feedback.value = ''; } catch { items.value = []; feedback.value = '读取失败，请检查会话范围与服务状态。'; } finally { busy.value = false; } }
function edit(item: Experience) { editing.value = item; summary.value = item.summary; confirmation.value = item.confirmation; expiresAt.value = item.expires_at ? new Date(new Date(item.expires_at * 1000).getTime() - new Date(item.expires_at * 1000).getTimezoneOffset() * 60000).toISOString().slice(0,16) : ''; }
async function revise() { const item = editing.value; if (!item) return; busy.value = true; try { await upgrades.reviseExperience(item.memory_id, loadedScope.value, {summary:summary.value,expected_revision:item.revision,confirmation:confirmation.value,...(expiresAt.value ? {expires_at:new Date(expiresAt.value).getTime() / 1000} : {})}); Object.assign(scope, loadedScope.value); await load(); feedback.value = '修订已保存。'; } catch { feedback.value = '修订未保存。请确认功能已启用，或刷新后处理版本冲突。'; } finally { busy.value = false; } }
async function forget() { const item = forgetTarget.value; if (!item) return; busy.value = true; try { await upgrades.forgetExperience(item.memory_id, loadedScope.value, item.revision); Object.assign(scope, loadedScope.value); await load(); feedback.value = '已遗忘。'; } catch { feedback.value = '遗忘未完成。请确认功能已启用，或刷新后处理版本冲突。'; } finally { busy.value = false; } }
</script>
