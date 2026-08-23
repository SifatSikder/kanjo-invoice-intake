"use client";

import { useCallback, useRef, useState } from "react";

export interface UploadOutcome {
  accepted: { filename: string; invoice_id: number; pages: number }[];
  rejected: { filename: string; reason: string; duplicate?: boolean; invoice_id?: number }[];
  processing: number;
}

/**
 * The way an invoice enters the system: someone has a document and drops it in.
 * The response comes back as soon as the file is stored, so the queue shows it
 * as "reading" straight away rather than hiding a ten-second extraction behind
 * a spinner with no sign the upload landed.
 */
export function UploadZone({
  onUploaded,
  compact,
}: {
  onUploaded: (outcome: UploadOutcome) => void;
  compact?: boolean;
}) {
  const input = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const send = useCallback(
    async (files: FileList | File[]) => {
      const list = Array.from(files);
      if (!list.length) return;
      setBusy(true);
      setError(null);
      try {
        const form = new FormData();
        list.forEach((f) => form.append("files", f));
        const res = await fetch("/api/documents", { method: "POST", body: form });
        if (!res.ok) {
          const body = await res.json().catch(() => ({}));
          throw new Error(body?.detail ?? `upload failed (${res.status})`);
        }
        onUploaded(await res.json());
      } catch (e) {
        setError(String(e instanceof Error ? e.message : e));
      } finally {
        setBusy(false);
        if (input.current) input.current.value = "";
      }
    },
    [onUploaded],
  );

  return (
    <div>
      <div
        className={`drop ${dragging ? "over" : ""} ${compact ? "compact" : ""} ${busy ? "busy" : ""}`}
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onMouseMove={(e) => {
          const r = e.currentTarget.getBoundingClientRect();
          e.currentTarget.style.setProperty("--mx", `${e.clientX - r.left}px`);
          e.currentTarget.style.setProperty("--my", `${e.clientY - r.top}px`);
        }}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          send(e.dataTransfer.files);
        }}
        onClick={() => input.current?.click()}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") input.current?.click();
        }}
      >
        <input
          ref={input}
          type="file"
          multiple
          accept=".pdf,.jpg,.jpeg,.png,.tif,.tiff,.webp"
          // Visually hidden rather than `hidden`: an element with the hidden
          // attribute is removed from the accessibility tree entirely and cannot
          // be reached with a keyboard.
          className="visually-hidden"
          onChange={(e) => e.target.files && send(e.target.files)}
        />
        {busy ? (
          <>
            <div className="drop-icon" aria-hidden>
              ↑
            </div>
            <div className="drop-title">Uploading…</div>
            <div className="drop-hint">Reading starts the moment it lands.</div>
          </>
        ) : (
          <>
            <div className="drop-icon" aria-hidden>
              ↑
            </div>
            <div className="drop-title">
              {compact ? "Add more invoices" : "Drop invoices here"}
            </div>
            <div className="drop-hint">
              or click to choose files · PDF, JPG, PNG, TIFF · several at once
            </div>
          </>
        )}
      </div>
      {error && <p className="note err-text">{error}</p>}
    </div>
  );
}
