import { DashboardClient } from "./ui/dashboard-client";

export default function HomePage() {
  if (!process.env.NEXT_PUBLIC_CONVEX_URL) {
    return (
      <main className="shell">
        <section className="hero">
          <span className="eyebrow">Monitor Multiple Runs</span>
          <h1>Marketplace of Ideas</h1>
          <p>
            Set <code>NEXT_PUBLIC_CONVEX_URL</code> to point the dashboard at your Convex deployment.
          </p>
        </section>
      </main>
    );
  }

  return <DashboardClient />;
}
