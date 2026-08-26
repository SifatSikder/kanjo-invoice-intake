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
              Where automation stops and you start.
            </p>
          </div>
          <button className="btn" onClick={onClose} aria-label="Close">
            Close
          </button>
        </div>

        <p className="note" style={{ marginTop: 0 }}>
          None of these came from your brief — they were our assumptions. Changing one
          re-checks every invoice that is not already filed. Nothing is filed as a
          result; an invoice that becomes eligible still waits for you.
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
                Off means nothing reaches the accounting system without a person, which
                is where a cautious first month would start.
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
                A control, not a correctness check — a large invoice gets a person however
                cleanly it was read. <strong>0</strong> turns it off.
              </p>
            </div>

            <div className="dial">
              <label htmlFor="p-conf">Review below confidence</label>
              <input
                id="p-conf"
                type="number"
                min={0}
                max={1}
                step={0.05}
                value={draft?.confidence_floor ?? 0}
                disabled={saving}
                onChange={(e) =>
                  setDraft((d) => d && { ...d, confidence_floor: Number(e.target.value) })
                }
              />
              <p className="dial-why">
                Weak in practice: the model reports near-certainty on almost everything,
                including an invoice whose own total does not add up. The arithmetic
                checks do the real work.
              </p>
            </div>

            <div className="dial">
              <label htmlFor="p-window">Near-duplicate window</label>
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
                Same supplier, same total, this close together gets flagged even when the
                invoice numbers differ — how a re-issued invoice actually looks.
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
            <p className="note">
              Last changed by <strong>{policy.updated_by}</strong>. Every change is
              recorded, so a filing can be read against the rules in force at the time.
            </p>
          </>
        )}
      </aside>
    </>
  );
}
