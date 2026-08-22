import "./globals.css";
import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Invoice Intake",
  description: "AI-assisted invoice intake with a verification gate",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
