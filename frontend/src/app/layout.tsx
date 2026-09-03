import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "MergeCut — Semantic merge checker",
  description:
    "Catch when two safe video edits combine to change what the video says.",
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
