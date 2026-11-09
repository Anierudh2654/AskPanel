import { create } from "zustand";

export type TextSelection = {
  messageId: string;
  text: string;
  start: number;
  end: number;
  prefix: string;
  suffix: string;
  rect: { top: number; left: number; right: number; bottom: number };
};

type UIState = {
  sidebarOpen: boolean;
  selection: TextSelection | null;
  toast: string | null;
  setSelection: (selection: TextSelection | null) => void;
  setToast: (message: string | null) => void;
  toggleSidebar: () => void;
};

export const useUIStore = create<UIState>((set) => ({
  sidebarOpen: true,
  selection: null,
  toast: null,
  setSelection: (selection) => set({ selection }),
  setToast: (toast) => set({ toast }),
  toggleSidebar: () => set((state) => ({ sidebarOpen: !state.sidebarOpen })),
}));
