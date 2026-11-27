import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";

import { useThemeStore } from "../store/themeStore";
import { THEME_KEY } from "../ui/theme";
import { ThemeToggle } from "./ThemeToggle";

describe("theme toggle", () => {
  afterEach(() => {
    cleanup();
    useThemeStore.getState().setTheme("light");
    localStorage.clear();
  });

  it("switches the whole app between light and dark and remembers it", async () => {
    const user = userEvent.setup();
    render(<ThemeToggle />);
    expect(screen.getByRole("button", { name: "Light" }).getAttribute("aria-pressed")).toBe("true");
    await user.click(screen.getByRole("button", { name: "Dark" }));
    expect(useThemeStore.getState().theme).toBe("dark");
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(localStorage.getItem(THEME_KEY)).toBe("dark");
    expect(screen.getByRole("button", { name: "Dark" }).getAttribute("aria-pressed")).toBe("true");
    await user.click(screen.getByRole("button", { name: "Light" }));
    expect(document.documentElement.dataset.theme).toBe("light");
    expect(localStorage.getItem(THEME_KEY)).toBe("light");
  });
});
