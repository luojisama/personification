import { beforeEach, describe, expect, it } from "vitest";
import { createPinia, setActivePinia } from "pinia";

import type { BotIdentity } from "@/api/types";
import { BOT_STORAGE_KEY, resolveSelectedBot, useBotStore } from "./bot";

const bots: BotIdentity[] = [
  { bot_id: "1", nickname: "一号", avatar_url: null, online: true, is_default: false, last_seen_at: 1 },
  { bot_id: "2", nickname: "二号", avatar_url: null, online: true, is_default: true, last_seen_at: 2 },
  { bot_id: "3", nickname: "三号", avatar_url: null, online: false, is_default: false, last_seen_at: null },
];

describe("resolveSelectedBot", () => {
  it("按已保存、默认、首个和空列表的顺序选择", () => {
    expect(resolveSelectedBot(bots, "3")?.bot_id).toBe("3");
    expect(resolveSelectedBot(bots, "missing")?.bot_id).toBe("2");
    expect(resolveSelectedBot(bots.map((bot) => ({ ...bot, is_default: false })), "")?.bot_id).toBe("1");
    expect(resolveSelectedBot([], "")).toBeNull();
  });
});

describe("useBotStore", () => {
  beforeEach(() => {
    window.localStorage.clear();
    setActivePinia(createPinia());
  });

  it("读取并更新所选 Bot", () => {
    window.localStorage.setItem(BOT_STORAGE_KEY, "2");
    const store = useBotStore();
    expect(store.selectedBotId).toBe("2");
    store.setBotId("3");
    expect(store.selectedBotId).toBe("3");
    expect(window.localStorage.getItem(BOT_STORAGE_KEY)).toBe("3");
    store.setBotId("");
    expect(window.localStorage.getItem(BOT_STORAGE_KEY)).toBeNull();
  });
});
