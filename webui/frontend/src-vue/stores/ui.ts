import { defineStore } from "pinia";
import { ref } from "vue";

export const useUiStore = defineStore("ui", () => {
  const sidebarCollapsed = ref(false);
  const commandPaletteOpen = ref(false);

  function toggleSidebar(): void {
    sidebarCollapsed.value = !sidebarCollapsed.value;
  }

  function setSidebarCollapsed(collapsed: boolean): void {
    sidebarCollapsed.value = collapsed;
  }

  function setCommandPaletteOpen(open: boolean): void {
    commandPaletteOpen.value = open;
  }

  return {
    sidebarCollapsed,
    commandPaletteOpen,
    toggleSidebar,
    setSidebarCollapsed,
    setCommandPaletteOpen,
  };
});
