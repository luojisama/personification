import { useQuery } from "@tanstack/react-query";
import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

import { resources } from "../api/resources";
import type { BotIdentity } from "../api/types";

const STORAGE_KEY = "personification:selected-bot";

interface BotContextValue {
  bots: BotIdentity[];
  selectedBot: BotIdentity | null;
  botId: string;
  setBotId: (botId: string) => void;
  isPending: boolean;
}

const BotContext = createContext<BotContextValue | null>(null);

export function BotProvider({ children }: { children: ReactNode }) {
  const query = useQuery({ queryKey: ["bots"], queryFn: ({ signal }) => resources.bots(signal), staleTime: 30_000 });
  const [botId, setBotIdState] = useState(() => window.localStorage.getItem(STORAGE_KEY) ?? "");
  const bots = query.data?.items ?? [];
  const selectedBot = useMemo(
    () => bots.find((bot) => bot.bot_id === botId) ?? bots.find((bot) => bot.is_default) ?? bots[0] ?? null,
    [botId, bots],
  );

  useEffect(() => {
    if (selectedBot && selectedBot.bot_id !== botId) setBotIdState(selectedBot.bot_id);
  }, [botId, selectedBot]);

  const setBotId = (value: string) => {
    setBotIdState(value);
    window.localStorage.setItem(STORAGE_KEY, value);
  };

  return (
    <BotContext.Provider value={{ bots, selectedBot, botId: selectedBot?.bot_id ?? botId, setBotId, isPending: query.isPending }}>
      {children}
    </BotContext.Provider>
  );
}

export function useBot() {
  const value = useContext(BotContext);
  if (!value) throw new Error("useBot must be used within BotProvider");
  return value;
}
