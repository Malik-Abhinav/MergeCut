import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "MergeCut",
  description:
    "Semantic three-way merge analyzer for video. Phase 0 shell.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}