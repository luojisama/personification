import { VueQueryPlugin } from "@tanstack/vue-query";
import { createPinia } from "pinia";
import { createApp } from "vue";

import App from "./App.vue";
import { queryClient } from "./app/queryClient";
import { createRuntimeEventsManager, provideRuntimeEvents } from "./realtime/runtimeEvents";
import { router } from "./router";
import { useThemeStore } from "./stores/theme";

const app = createApp(App);
const pinia = createPinia();

app.use(pinia);
app.use(VueQueryPlugin, { queryClient });
app.use(router);

const runtimeEventsManager = createRuntimeEventsManager(queryClient);
provideRuntimeEvents(app, runtimeEventsManager);
runtimeEventsManager.start();

const themeStore = useThemeStore(pinia);
themeStore.init();

function disposeApplicationServices(): void {
  runtimeEventsManager.stop();
  themeStore.dispose();
}

window.addEventListener("pagehide", disposeApplicationServices, { once: true });
app.mount("#vue-root");
