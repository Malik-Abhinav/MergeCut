/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Phase 0/1 only — the frontend shell exists to be wired up later.
  // Real routes (upload, timeline, conflicts, resolution, verification)
  // are deferred to Phase 8 per PROJECT_PLAN §29.
};

export default nextConfig;