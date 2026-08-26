"use client";

import { useCallback, useEffect, useState } from "react";

export interface Policy {
  auto_post_enabled: boolean;
  amount_review_threshold_jpy: number;
  confidence_floor: number;
  near_duplicate_window_days: number;
  updated_by: string;
}

/**
 * The dials that decide where automation stops and a person starts.
 *
 * None of these numbers came from the client — the brief states no approval
 * limit, no confidence floor and no duplicate window, and there was nobody to
 * ask. They were assumptions, and assumptions belong to whoever runs the
 * process, not to whoever built it. Changing one re-judges every invoice that
 * is not already filed, so the queue always reflects the rule in force rather
 * than the rule that happened to apply the day a document arrived.
 */
export function PolicyPanel({
  open,
  onClose,
  onChanged,
}: {
  open: boolean;
  onClose: () => void;
  onChanged: () => void;
}) {
  const [policy, setPolicy] = useState<Policy | null>(null);
  const [draft, setDraft] = useState<Policy | null>(null);
  const [saving, setSaving] = useState(false);
  const [result, setResult] = useState<string | null>(null);

  /* Edits are held until Apply rather than saved on blur. Each change re-checks
     the whole queue, so four dials adjusted together should cost one pass, not
     four -- and a value typed then abandoned should not take effect because the
     field happened to lose focus. */
  const dirty =
    !!policy && !!draft && (Object.keys(policy) as (keyof Policy)[])
      .some((k) => k !== "updated_by" && policy[k] !== draft[k]);

  const load = useCallback(async () => {
    try {
      const p = await fetch("/api/policy", { cache: "no-store" }).then((r) => r.json());
      setPolicy(p);
      setDraft(p);
    } catch {
      /* the dashboard already reports an unreachable API */
    }
  }, []);

  useEffect(() => {
    if (open) {
      load();
      setResult(null);
    }
  }, [open, load]);

  // Escape closes it, the way every drawer should.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  async function apply() {
    if (!draft) return;
    setSaving(true);
    try {
      const res = await fetch("/api/policy", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          auto_post_enabled: draft.auto_post_enabled,
          amount_review_threshold_jpy: draft.amount_review_threshold_jpy,
          confidence_floor: draft.confidence_floor,
          near_duplicate_window_days: draft.near_duplicate_window_days,
          actor: "reviewer",
        }),
      });
      const data = await res.json();
      setPolicy(data.policy);
      setDraft(data.policy);
      setResult(
        data.changed
          ? data.moved > 0
            ? `${data.moved} invoice(s) changed where they sit, out of ${data.reverified} re-checked.`
            : `${data.reverified} invoice(s) re-checked; none changed where they sit.`
          : "No change.",
      );
      onChanged();
    } finally {
      setSaving(false);
    }
  }

  if (!open) return null;

  return (
    <>
      <div className="scrim" onClick={onClose} aria-hidden />
      <aside className="drawer" role="dialog" aria-label="Review policy">
        <div className="drawer-head">
          <div>
            <h2 style={{ margin: 0 }}>Review policy</h2>
            <p className="sub" style={{ fontSize: 13 }}>
              What gets filed for you, and what comes to you first.
            </p>
          </div>
          <button className="btn" onClick={onClose} aria-label="Close">
            Close
          </button>
        </div>

        <p className="note" style={{ marginTop: 0 }}>
          These decide which invoices are filed for you and which come to you first.
          Changing one re-checks everything still open. Nothing is filed as a result —
          an invoice that becomes eligible still waits for you to send it.
        </p>

        {!policy ? (
          <p className="note">Loading…</p>
        ) : (
          <>
            <div className="dial">
              <label htmlFor="p-auto">File automatically</label>
              <select
                id="p-auto"
                value={draft?.auto_post_enabled ? "yes" : "no"}
                disabled={saving}
                onChange={(e) =>
                  setDraft((d) => d && { ...d, auto_post_enabled: e.target.value === "yes" })
                }
              >
                <option value="yes">Yes — file when every check passes</option>
                <option value="no">No — send everything to me</option>
              </select>
              <p className="dial-why">
                Turn this off and nothing reaches the accounting system until you send it
                yourself.
              </p>
            </div>

            <div className="dial">
              <label htmlFor="p-amount">Always review above</label>
              <div className="dial-input">
                <span className="prefix">¥</span>
                <input
                  id="p-amount"
                  type="number"
                  min={0}
                  step={100000}
                  value={draft?.amount_review_threshold_jpy ?? 0}
                  disabled={saving}
                  onChange={(e) =>
                    setDraft((d) => d && {
                      ...d, amount_review_threshold_jpy: Number(e.target.value),
                    })
                  }
                />
              </div>
              <p className="dial-why">
                Anything above this comes to you first, however cleanly it was read. Set
                it to <strong>0</strong> to turn this off.
              </p>
            </div>

            <div className="dial">
              <label htmlFor="p-conf">Send to me when the reading looks unsure</label>
              <div className="dial-input">
                {/* Shown as a percentage. A reviewer thinks in "how sure is it",
                    not in a 0-to-1 float. */}
                <input
                  id="p-conf"
                  type="number"
                  min={0}
                  max={100}
                  step={5}
                  value={Math.round((draft?.confidence_floor ?? 0) * 100)}
                  disabled={saving}
                  onChange={(e) =>
                    setDraft((d) => d && { ...d, confidence_floor: Number(e.target.value) / 100 })
                  }
                />
                <span className="suffix">% sure or less</span>
              </div>
              <p className="dial-why">
                The reader scores how clearly it could make out each field. Anything at or
                below this comes to you instead of being filed. Be aware it rarely admits
                doubt, so this is a backstop — the arithmetic checks catch far more.
              </p>
            </div>

            <div className="dial">
              <label htmlFor="p-window">Watch for the same bill arriving twice within</label>
              <div className="dial-input">
                <input
                  id="p-window"
                  type="number"
                  min={0}
                  max={365}
                  value={draft?.near_duplicate_window_days ?? 0}
                  disabled={saving}
                  onChange={(e) =>
                    setDraft((d) => d && {
                      ...d, near_duplicate_window_days: Number(e.target.value),
                    })
                  }
                />
                <span className="suffix">days</span>
              </div>
              <p className="dial-why">
                If a supplier sends another bill for exactly the same amount within this
                many days, you get told — even when the invoice number is different, which
                is what a re-sent or re-issued bill looks like.
              </p>
            </div>

            <div className="apply-bar">
              <button
                className="btn primary"
                disabled={!dirty || saving}
                onClick={apply}
              >
                {saving ? "Re-checking the queue…" : "Apply"}
              </button>
              {dirty && !saving && (
                <button className="btn" onClick={() => setDraft(policy)}>
                  Discard
                </button>
              )}
            </div>

            {result && <div className="banner ok">{result}</div>}
          </>
        )}
      </aside>
    </>
  );
}
