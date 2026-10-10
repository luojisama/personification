import { mount, flushPromises } from '@vue/test-utils';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { upgrades } from '@/api/upgrades';
import ConnectionRoomsPanel from './ConnectionRoomsPanel.vue';
vi.mock('@/api/upgrades', () => ({upgrades:{rooms:vi.fn(),saveRoom:vi.fn(),moderate:vi.fn()}}));
const button = (wrapper: ReturnType<typeof mount>, text:string) => wrapper.findAll('button').find(b=>b.text()===text)!;
describe('Telegram 房间设置',()=>{
 beforeEach(()=>{vi.clearAllMocks();vi.mocked(upgrades.rooms).mockResolvedValue({items:[]});});
 it('未展开不读取，默认shadow，保存不打开主动推送',async()=>{
 const wrapper=mount(ConnectionRoomsPanel,{props:{connectionId:'m'}}); expect(upgrades.rooms).not.toHaveBeenCalled();
 const details=wrapper.find('details'); (details.element as HTMLDetailsElement).open=true; await details.trigger('toggle'); await flushPromises();
 expect(upgrades.rooms).toHaveBeenCalledWith('m'); await wrapper.find('input').setValue('-100'); await wrapper.findAll('input')[1]!.setValue('4'); await wrapper.find('form').trigger('submit'); await flushPromises();
 expect(upgrades.saveRoom).toHaveBeenCalledWith('m',{chat_id:'-100',topic_id:'4',participation:'shadow',proactive_enabled:false});
 });
 it('真实管理必须明确确认并冻结预览目标，未知结果不重试',async()=>{
 vi.mocked(upgrades.moderate).mockResolvedValue({state:'unknown',code:'telegram_delivery_unknown',message_id:''});
 const wrapper=mount(ConnectionRoomsPanel,{props:{connectionId:'m'}}); await wrapper.find('input').setValue('-100'); const forms=wrapper.findAll('form'); await forms[1]!.find('input').setValue('123'); await forms[1]!.trigger('submit');
 expect(upgrades.moderate).not.toHaveBeenCalled(); await wrapper.find('input').setValue('-200'); await button(wrapper,'确认执行').trigger('click'); await flushPromises();
 expect(upgrades.moderate).toHaveBeenCalledWith('m',expect.objectContaining({action:'mute',chat_id:'-100',target_id:'123'})); expect(upgrades.moderate).toHaveBeenCalledTimes(1); expect(wrapper.text()).toContain('执行结果未知'); expect(button(wrapper,'确认执行')).toBeUndefined();
 });
 it('群主动开关显式启用，私聊保持关闭',async()=>{
 const wrapper=mount(ConnectionRoomsPanel,{props:{connectionId:'m'}}); const chat=wrapper.find('input'); const toggle=wrapper.find('input[type=checkbox]');
 await chat.setValue('-100'); await toggle.setValue(true); await wrapper.find('form').trigger('submit'); await flushPromises(); expect(upgrades.saveRoom).toHaveBeenLastCalledWith('m',expect.objectContaining({proactive_enabled:true}));
 await chat.setValue('123'); expect(toggle.attributes('disabled')).toBeDefined(); await wrapper.find('form').trigger('submit'); await flushPromises(); expect(upgrades.saveRoom).toHaveBeenLastCalledWith('m',expect.objectContaining({proactive_enabled:false})); expect(wrapper.text()).toContain('需要同时在配置中开启插件、主动消息和群聊空闲发话');
 });

});
