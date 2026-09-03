import { defineStore } from "pinia";
import { ref } from "vue";

import type { BotIdentity } from "@/api/types";

export const BOT_STORAGE_KEY = "personification:selected-bot";

function readStoredBotId(): string {
  try {
    return window.localStorage.getItem(BOT_STORAGE_KEY) ?? "";
  } catch {
    return "";
  }
}

function writeStoredBotId(value: string): void {
  try {
    if (value) window.localStorage.setItem(BOT_STORAGE_KEY, value);
    else window.localStorage.removeItem(BOT_STORAGE_KEY);
  } catch {
    // 本地存储不可用时仍保留本次页面内选择。
  }
}

export function resolveSelectedBot(
  bots: readonly BotIdentity[],
  selectedBotId: string,
): BotIdentity | null {
  return (
    bots.find((bot) => bot.bot_id === selectedBotId) ??
    bots.find((bot) => bot.is_default) ??
    bots[0] ??
    null
  );
}

export const useBotStore = defineStore("bot", () => {
  const selectedBotId = ref(readStoredBotId());

  function setBotId(value: string): void {
    selectedBotId.value = value;
    writeStoredBotId(value);
  }

  return { selectedBotId, setBotId };
});
