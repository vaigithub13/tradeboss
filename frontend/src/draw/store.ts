import { create } from "zustand";

import { fetchDrawings, importDrawings, saveDrawings } from "./api";
import {
  TWO_ANCHOR,
  defaultStyle,
  editDrawing,
  removeDrawing,
  type Anchor,
  type DrawDoc,
  type DrawTool,
  type Drawing,
} from "./model";

export type DrawMode = DrawTool | "cursor" | "eraser";

interface Snap {
  drawings: Drawing[];
  lockAll: boolean;
  hideAll: boolean;
}

interface DrawState extends Snap {
  symbol: string;
  past: Snap[];
  future: Snap[];
  tool: DrawMode;
  magnet: boolean;
  selectedId: string | null;
  draft: Anchor | null;
  hover: Anchor | null;
  holidays: string[];
  ready: boolean;
  setTool: (tool: DrawMode) => void;
  setMagnet: (on: boolean) => void;
  setHover: (anchor: Anchor | null) => void;
  setSelected: (id: string | null) => void;
  setHolidays: (days: string[]) => void;
  place: (anchor: Anchor, knownAt: number) => void;
  preview: (drawings: Drawing[]) => void;
  remember: () => void;
  remove: (id: string) => void;
  changeSelected: (patch: Partial<Drawing>) => void;
  toggleLock: () => void;
  toggleHide: () => void;
  undo: () => void;
  redo: () => void;
  load: (symbol: string) => Promise<void>;
  save: () => Promise<void>;
  importJson: (payload: unknown) => Promise<void>;
}

let loadToken = 0;

function make(tool: DrawTool, anchors: Anchor[], knownAt: number): Drawing {
  return {
    id: `d-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`,
    tool,
    anchors,
    knownAt,
    text: tool === "text" ? "Text" : "",
    style: defaultStyle(tool),
  };
}

function docOf(s: Snap & { symbol: string }): DrawDoc {
  return { symbol: s.symbol, drawings: s.drawings, lockAll: s.lockAll, hideAll: s.hideAll };
}

export const useDrawStore = create<DrawState>((set, get) => {
  const snap = (): Snap => {
    const s = get();
    return { drawings: s.drawings, lockAll: s.lockAll, hideAll: s.hideAll };
  };
  const commit = (next: Partial<Snap> & { selectedId?: string | null; draft?: Anchor | null }): void => {
    const current = snap();
    set({ past: [...get().past, current], future: [], ...next });
  };

  return {
    symbol: "",
    drawings: [],
    lockAll: false,
    hideAll: false,
    past: [],
    future: [],
    tool: "cursor",
    magnet: false,
    selectedId: null,
    draft: null,
    hover: null,
    holidays: [],
    ready: false,
    setTool: (tool) => set({ tool, draft: null, hover: null }),
    setMagnet: (on) => set({ magnet: on }),
    setHover: (hover) => set({ hover }),
    setSelected: (selectedId) => set({ selectedId }),
    setHolidays: (holidays) => set({ holidays }),
    place: (anchor, knownAt) => {
      const s = get();
      if (!s.ready || s.lockAll) return;
      const tool = s.tool;
      if (tool === "cursor" || tool === "eraser") return;
      if (!TWO_ANCHOR.has(tool)) {
        const drawing = make(tool, [anchor], knownAt);
        commit({ drawings: [...s.drawings, drawing], selectedId: drawing.id });
        return;
      }
      if (!s.draft) {
        set({ draft: anchor });
        return;
      }
      const drawing = make(tool, [s.draft, anchor], knownAt);
      commit({ drawings: [...s.drawings, drawing], selectedId: drawing.id, draft: null });
      set({ hover: null });
    },
    preview: (drawings) => set({ drawings }),
    remember: () => {
      const current = snap();
      set({ past: [...get().past, current], future: [] });
    },
    remove: (id) => {
      const s = get();
      const current = docOf(s);
      const next = removeDrawing(current, id);
      if (next === current) return;
      commit({ drawings: next.drawings, selectedId: s.selectedId === id ? null : s.selectedId });
    },
    changeSelected: (patch) => {
      const s = get();
      if (!s.selectedId) return;
      const current = docOf(s);
      const next = editDrawing(current, s.selectedId, patch);
      if (next === current) return;
      commit({ drawings: next.drawings });
    },
    toggleLock: () => commit({ lockAll: !get().lockAll }),
    toggleHide: () => commit({ hideAll: !get().hideAll }),
    undo: () => {
      const s = get();
      const prev = s.past[s.past.length - 1];
      if (!prev) return;
      set({
        past: s.past.slice(0, -1),
        future: [snap(), ...s.future],
        drawings: prev.drawings,
        lockAll: prev.lockAll,
        hideAll: prev.hideAll,
        draft: null,
      });
    },
    redo: () => {
      const s = get();
      const next = s.future[0];
      if (!next) return;
      set({
        future: s.future.slice(1),
        past: [...s.past, snap()],
        drawings: next.drawings,
        lockAll: next.lockAll,
        hideAll: next.hideAll,
        draft: null,
      });
    },
    load: async (symbol) => {
      const token = ++loadToken;
      set({
        symbol,
        ready: false,
        drawings: [],
        past: [],
        future: [],
        selectedId: null,
        draft: null,
        hover: null,
      });
      try {
        const payload = await fetchDrawings(symbol);
        if (token !== loadToken) return;
        set({ drawings: payload.drawings, ready: true });
      } catch {
        if (token !== loadToken) return;
        set({ ready: false });
      }
    },
    save: async () => {
      const s = get();
      if (!s.ready || s.symbol === "") return;
      await saveDrawings(s.symbol, s.drawings);
    },
    importJson: async (payload) => {
      const saved = await importDrawings(payload as { symbol: string; drawings: Drawing[] });
      if (saved.symbol === get().symbol) {
        set({ drawings: saved.drawings, past: [], future: [], selectedId: null, draft: null, ready: true });
      }
    },
  };
});
