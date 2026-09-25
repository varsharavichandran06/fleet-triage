import type { Metadata } from "next";
import "./globals.css";

// System font stack rather than next/font/google: that fetches Geist from
// Google Fonts at build time, which fails in sandboxed or offline build
// environments.

export const metadata: Metadata = {
  title: "fleet-triage",
  description: "Failure triage for GPU training clusters",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className="h-full antialiased">
      <body className="min-h-full flex flex-col font-sans">{children}</body>
    </html>
  );
}
