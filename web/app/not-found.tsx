import Link from "next/link";

export default function NotFound() {
  return (
    <main className="wrap">
      <div className="panel rise" style={{ marginTop: 24, textAlign: "center", padding: 44 }}>
        <h1 style={{ marginBottom: 10 }}>That invoice isn&rsquo;t here</h1>
        <p className="sub" style={{ marginBottom: 20 }}>
          It may have been cleared from the queue, or the link may be out of date.
        </p>
        <Link href="/" className="btn primary">
          Back to the inbox
        </Link>
      </div>
    </main>
  );
}
