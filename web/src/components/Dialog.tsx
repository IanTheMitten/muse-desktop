import { useEffect, useId, useRef, type ReactNode } from "react";
import "../styles/dialogs.css";

export function Dialog({
  title,
  onClose,
  children,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();

  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null;
    const dlg = ref.current;
    if (dlg && !dlg.open) dlg.showModal();
    return () => opener?.focus?.();
  }, []);

  // Esc fires cancel then close, so close alone covers both.
  return (
    <dialog ref={ref} className="dlg" aria-labelledby={titleId} onClose={onClose}>
      <h2 id={titleId} className="serif dlg-title">{title}</h2>
      {children}
    </dialog>
  );
}
