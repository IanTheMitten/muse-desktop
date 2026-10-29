/* The desktop shell's API, or undefined in a plain browser. Tauri injects
 * window.__TAURI__ (app.withGlobalTauri) into every window, the sidecar's pages
 * included; nothing imports @tauri-apps/api. */

export type ShellCommand = "toggle" | "next" | "previous";

export type NowState = {
  track: {
    track_rowid: number;
    track_name: string;
    artist_name: string;
    has_art: boolean;
  } | null;
  playing: boolean;
};

export type DragDrop =
  | { type: "enter"; paths: string[] }
  | { type: "over" }
  | { type: "drop"; paths: string[] }
  | { type: "leave" };

type Tauri = {
  core: { invoke<T>(cmd: string, args?: Record<string, unknown>): Promise<T> };
  event: {
    listen<T>(event: string, cb: (e: { payload: T }) => void): Promise<() => void>;
    emitTo(target: string, event: string, payload?: unknown): Promise<void>;
  };
  webview: {
    getCurrentWebview(): {
      onDragDropEvent(cb: (e: { payload: DragDrop }) => void): Promise<() => void>;
    };
  };
  window: { getCurrentWindow(): { close(): Promise<void> } };
  dialog: {
    open(o: { directory: boolean; multiple: boolean }): Promise<string | string[] | null>;
  };
};

export const shell: Tauri | undefined = (window as unknown as { __TAURI__?: Tauri }).__TAURI__;
