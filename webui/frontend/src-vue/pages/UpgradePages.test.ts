import { mount, flushPromises } from '@vue/test-utils';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { upgrades } from '@/api/upgrades';
import ConnectionsPage from './ConnectionsPage.vue';
import ExperiencesPage from './ExperiencesPage.vue';
import QualityPage from './QualityPage.vue';
vi.mock('@/api/upgrades', () => ({upgrades: {connections:vi.fn(),probe:vi.fn(),saveConnection:vi.fn(),connectionAction:vi.fn(),updateConnection:vi.fn(),deleteConnection:vi.fn(),experiences:vi.fn(),reviseExperience:vi.fn(),forgetExperience:vi.fn(),quality:vi.fn(),saveCase:vi.fn(),previewQuality:vi.fn(),startQuality:vi.fn(),qualityJob:vi.fn(),scoreQuality:vi.fn(),qualityReport:vi.fn(),cancelQuality:vi.fn()}}));
const button = (wrapper: ReturnType<typeof mount>, text: string) => wrapper.findAll('button').find(b => b.text() === text)!;
describe('角色体验工作台', () => {
 beforeEach(() => {vi.clearAllMocks(); vi.mocked(upgrades.connections).mockResolvedValue({items:[]}); vi.mocked(upgrades.quality).mockResolvedValue({cases:[],jobs:[],providers:[],dimensions:[]});});
 it('原生连接不显示启停，未测试项目不误报通过', async () => {
 vi.mocked(upgrades.connections).mockResolvedValue({items:[{id:'native',bot_id:'123',source:'native',test_url:'javascript:alert(1)'}]});
 const wrapper = mount(ConnectionsPage); await flushPromises();
 expect(wrapper.text()).toContain('由 NoneBot 配置管理'); expect(wrapper.text()).toContain('未验证'); expect(button(wrapper,'启用')).toBeUndefined(); expect(wrapper.find('a').exists()).toBe(false);
 });
 it('Token 只在探测与保存时提交，保存后清空', async () => {
 vi.mocked(upgrades.probe).mockResolvedValue({status:{credentials:'verified'}}); vi.mocked(upgrades.saveConnection).mockResolvedValue({connection:{id:'x',bot_id:'1',source:'managed'}});
 const storage = vi.spyOn(Storage.prototype,'setItem'); const wrapper = mount(ConnectionsPage); await flushPromises(); const token = wrapper.find('input[type=password]'); await token.setValue('123:example');
 await button(wrapper,'1. 检查连接').trigger('click'); await flushPromises(); await wrapper.find('form').trigger('submit'); await flushPromises();
 expect(upgrades.saveConnection).toHaveBeenCalledWith(expect.objectContaining({token:'123:example'})); expect((token.element as HTMLInputElement).value).toBe(''); expect(storage).not.toHaveBeenCalled(); storage.mockRestore();
 });
 it('修订绑定已加载范围而非后来修改的查询条件', async () => {
 vi.mocked(upgrades.experiences).mockResolvedValue({items:[{memory_id:'m',summary:'旧经历',participants:[],source_refs:[],revision:2,confirmation:'pending',kind:'experience'}]});
 const wrapper = mount(ExperiencesPage); const fields = wrapper.findAll('input'); await fields[0]!.setValue('bot1'); await fields[1]!.setValue('chat1'); await wrapper.find('form').trigger('submit'); await flushPromises();
 await fields[1]!.setValue('chat2'); await button(wrapper,'修订').trigger('click'); await wrapper.find('textarea').setValue('已修正'); await wrapper.findAll('form')[1]!.trigger('submit'); await flushPromises();
 expect(upgrades.reviseExperience).toHaveBeenCalledWith('m',expect.objectContaining({group_id:'chat1'}),expect.objectContaining({summary:'已修正',expected_revision:2}));
 });
 it('保存案例需要显式脱敏，打开实验室不调用模型', async () => {
 const wrapper = mount(QualityPage); await flushPromises(); expect(button(wrapper,'保存案例').attributes('disabled')).toBeDefined(); expect(upgrades.startQuality).not.toHaveBeenCalled(); expect(upgrades.previewQuality).not.toHaveBeenCalled(); wrapper.unmount();
 });
 it('盲评显示候选而不展示版本配置', async () => {
 vi.mocked(upgrades.quality).mockResolvedValue({cases:[],jobs:[{id:'j',model:'test-model',status:'completed',request_budget:3}],providers:[],dimensions:[]});
 vi.mocked(upgrades.qualityJob).mockResolvedValue({id:'j',model:'test-model',status:'completed',request_budget:3,ballots:[{id:'b',case_id:'c',candidates:[{id:'opaque1',text:'候选正文',status:'ok'}]}]});
 const wrapper = mount(QualityPage); await flushPromises(); await button(wrapper,'test-model · 已完成').trigger('click'); await flushPromises(); expect(wrapper.text()).toContain('候选正文'); expect(wrapper.text()).not.toContain('opaque1'); expect(wrapper.text()).toContain('都很差'); wrapper.unmount();
 });
 it('Webhook 迁移必须先在页面明确确认', async () => {
 vi.mocked(upgrades.connections).mockResolvedValue({items:[{id:'m',bot_id:'1',source:'managed',state:'error',last_error:'webhook_conflict'}]});
 const wrapper = mount(ConnectionsPage); await flushPromises(); await button(wrapper,'迁移现有 Webhook').trigger('click'); expect(upgrades.connectionAction).not.toHaveBeenCalled(); expect(wrapper.text()).toContain('将删除此 Bot 的现有 Webhook'); await button(wrapper,'确认迁移并启用').trigger('click'); await flushPromises(); expect(upgrades.connectionAction).toHaveBeenCalledWith('m','start',{migrate_webhook:true});
 });
 it('停止态编辑保留未展示的凭据与代理', async () => {
 vi.mocked(upgrades.connections).mockResolvedValue({items:[{id:'m',bot_id:'1',name:'旧名称',persona:'人设',scope:'all',source:'managed',state:'stopped'}]});
 const wrapper = mount(ConnectionsPage); await flushPromises(); await button(wrapper,'编辑').trigger('click'); await wrapper.findAll('form').at(-1)!.find('input').setValue('新名称'); await wrapper.findAll('form').at(-1)!.trigger('submit'); await flushPromises(); expect(upgrades.updateConnection).toHaveBeenCalledWith('m',{name:'新名称',persona:'人设',scope:'all'});
 });

});
