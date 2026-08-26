"use client";

import { useCallback, useEffect, useRef } from "react";

/**
 * A confirmation dialog.
 *
 * Built rather than pulled in, because the whole interface is one visual
 * language and a stock modal would arrive speaking a different one. What a
 * library would have given for free is here on purpose: focus moves into the
 * dialog and returns to whatever opened it, Tab cannot escape while it is open,
 * Escape and the backdrop both dismiss, and the destructive action is never the
 * one your fingers land on by default.
 */
export function Confirm({
  open,
  title,
  body,
  confirmLabel = "Confirm",
  cancelLabel = "Cancel",
  destructive = false,
  busy = false,
  onConfirm,
  onCancel,
}: {
  open: boolean;
  title: string;
  body: React.ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  destructive?: boolean;
  busy?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const dialog = useRef<HTMLDivElement>(null);
  const cancelBtn = useRef<HTMLButtonElement>(null);
  const opener = useRef<HTMLElement | null>(null);

  // Remember what had focus, and give it back on close -- otherwise a keyboard
  // user is dropped at the top of the document afterwards. Handled as an
  // explicit open/close transition rather than in effect cleanup: cleanup runs
  // while React is still committing, and a focus() call there lands on nothing.
  const wasOpen = useRef(false);
  useEffect(() => {
    if (open && !wasOpen.current) {
      opener.current = document.activeElement as HTMLElement;
      cancelBtn.current?.focus(); // Cancel, never the destructive button
    } else if (!open && wasOpen.current) {
      const target = opener.current;
      if (target?.isConnected) target.focus();
      opener.current = null;
    }
    wasOpen.current = open;
  }, [open]);

  const onKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onCancel();
        return;
      }
      if (e.key !== "Tab" || !dialog.current) return;
      // Keep Tab inside the dialog while it is open.
      const focusable = dialog.current.querySelectorAll<HTMLElement>(
        'button:not([disabled]), [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
      );
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    },
    [onCancel],
  );

  if (!open) return null;

  return (
    <div className="modal-scrim" onClick={onCancel} onKeyDown={onKeyDown}>
      <div
        ref={dialog}
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="confirm-title"
        aria-describedby="confirm-body"
        onClick={(e) => e.stopPropagation()}
      >
        <div className={`modal-mark ${destructive ? "danger" : ""}`} aria-hidden>
          {destructive ? "!" : "?"}
        </div>
        <h2 id="confirm-title">{title}</h2>
        <div id="confirm-body" className="modal-body">
          {body}
        </div>
        <div className="modal-actions">
          <button ref={cancelBtn} className="btn" onClick={onCancel} disabled={busy}>
            {cancelLabel}
          </button>
          <button
            className={`btn ${destructive ? "destructive" : "primary"}`}
            onClick={onConfirm}
            disabled={busy}
          >
            {busy ? "Working…" : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
