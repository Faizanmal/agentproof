import type { Metadata } from "next";
import { SiteHeader } from "@/components/site-header";
import { ThemeProvider } from "@/components/theme-provider";
import "./globals.css";

export const metadata: Metadata = {
  title: {
    default: "AgentProof — Evidence before confidence",
    template: "%s | AgentProof",
  },
  description:
    "A foundation for authorized, isolated browser QA with reproducible evidence.",
};
export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body>
        <ThemeProvider>
          <a className="skip-link" href="#main">
            Skip to content
          </a>
          <div className="site-wrap">
            <SiteHeader />
            <div id="main">{children}</div>
            <footer className="site-footer">
              <span>AgentProof / Authorized browser QA</span>
              <span>Evidence before confidence</span>
            </footer>
          </div>
        </ThemeProvider>
      </body>
    </html>
  );
}
