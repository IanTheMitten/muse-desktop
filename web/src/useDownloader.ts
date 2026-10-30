import { useEffect, useState } from "react";

import { getSettings } from "./api";

export const SETTINGS_CHANGED = "muse:settings-changed";

/** Tell every reader of the settings (nav, Add route, Home) to look again. */
export function settingsChanged(): void {
  window.dispatchEvent(new Event(SETTINGS_CHANGED));
}

/** null until the first answer; false when the answer is an error. */
export function useDownloaderUnlocked(): boolean | null {
  const [unlocked, setUnlocked] = useState<boolean | null>(null);
  useEffect(() => {
    let abort = new AbortController();
    const load = () => {
      abort.abort();
      abort = new AbortController();
      const mine = abort;
      getSettings(mine.signal).then(
        (s) => { if (!mine.signal.aborted) setUnlocked(s.downloader_unlocked); },
        () => { if (!mine.signal.aborted) setUnlocked(false); },
      );
    };
    load();
    window.addEventListener(SETTINGS_CHANGED, load);
    return () => {
      window.removeEventListener(SETTINGS_CHANGED, load);
      abort.abort();
    };
  }, []);
  return unlocked;
}
