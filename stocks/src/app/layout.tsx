import type { Metadata } from "next";
import "@fontsource/dm-sans/400.css";
import "@fontsource/dm-sans/500.css";
import "@fontsource/dm-sans/600.css";
import "@fontsource/manrope/600.css";
import "@fontsource/manrope/700.css";
import "react-grid-layout/css/styles.css";
import "react-resizable/css/styles.css";
import "./globals.css";
import "./research.css";
import "./portfolio.css";
export const metadata: Metadata = {
  title: "Folio — Your market, your workspace",
  description:
    "A personal, modular stock research workspace. Arrange your markets, watchlists, charts, and research your way.",
};
export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
