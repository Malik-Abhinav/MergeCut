export default function Phase0Shell() {
  return (
    <main
      style={{
        maxWidth: 720,
        margin: "10vh auto",
        padding: "2rem",
        background: "var(--card)",
        border: "1px solid var(--border)",
        borderRadius: 12,
      }}
    >
      <h1 style={{ marginTop: 0 }}>MergeCut</h1>
      <p style={{ color: "var(--muted)" }}>
        Semantic three-way merge analyzer for video. Built for MiniMax Week
        using MiniMax M3 through GMI Cloud.
      </p>
      <p>
        This is the Phase 0 frontend shell. The real upload, timeline, and
        conflict-resolution UI is wired up in Phase 8 per the build order in
        <code> PROJECT_PLAN.md </code>.
      </p>
    </main>
  );
}