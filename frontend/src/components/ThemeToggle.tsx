import { useThemeStore } from "../store/themeStore";

/** Light / Dark switch for the whole app. The Learn tab keeps its own look. */
export function ThemeToggle() {
  const theme = useThemeStore((s) => s.theme);
  const setTheme = useThemeStore((s) => s.setTheme);
  return (
    <div className="flex items-center gap-0.5 rounded border border-white/15 p-0.5" role="group" aria-label="Theme" data-testid="theme-toggle">
      {(["light", "dark"] as const).map((name) => (
        <button
          key={name}
          type="button"
          aria-pressed={theme === name}
          className={`rounded px-2 py-0.5 text-xs ${theme === name ? "bg-white/15 font-semibold text-white" : "text-white/60"}`}
          onClick={() => setTheme(name)}
        >
          {name === "light" ? "Light" : "Dark"}
        </button>
      ))}
    </div>
  );
}
