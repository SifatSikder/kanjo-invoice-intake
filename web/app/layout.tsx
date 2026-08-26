import "./globals.css";
import type { Metadata } from "next";
import Link from "next/link";

export const metadata: Metadata = {
  title: "Kanjo — invoice intake",
  description:
    "Reads supplier invoices, checks them against your accounting system's own rules, " +
    "and files only what it can verify.",
};

/** 勘定 (kanjō) — "the account", "the reckoning". What you ask for when you want
 *  the bill settled. */
export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <header className="masthead">
          <div className="masthead-in">
            <Link href="/" className="mark" aria-hidden tabIndex={-1}>
              勘
            </Link>
            <Link href="/" className="wordmark">
              Kanjo
              <span className="jp">勘定</span>
            </Link>
            <span className="spacer" />
          </div>
        </header>
        {children}
      </body>
    </html>
  );
}
