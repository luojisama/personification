import { createRouter, createWebHistory } from "vue-router";

import { routes } from "./routes";

export const BASE_URL = "/personification/frontend-vue-preview/";

export const router = createRouter({
  history: createWebHistory(BASE_URL),
  routes,
  scrollBehavior(_to, _from, savedPosition) {
    return savedPosition ?? { top: 0 };
  },
});

router.afterEach((route) => {
  const title = typeof route.meta.title === "string" ? route.meta.title : "事件取证台";
  document.title = `${title} · Personification`;
});
