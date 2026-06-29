import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Freight Carrier Agent",
  description: "Intake assistant for a freight broker's inbound carrier queue.",
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
