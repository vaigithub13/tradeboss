import { create } from "zustand";

import { applyDocumentTheme, loadTheme, saveTheme, type AppTheme } from "../ui/theme";

interface ThemeState {
  theme: AppTheme;
  setTheme: (theme: AppTheme) => void;
  toggle: () => void;
}

export const useThemeStore = create<ThemeState>((set, get) => ({
  theme: loadTheme(),
  setTheme: (theme) => {
    saveTheme(theme);
    applyDocumentTheme(theme);
    set({ theme });
  },
  toggle: () => get().setTheme(get().theme === "light" ? "dark" : "light"),
}));
