import "./globals.css";

export const metadata = {
  title: "Thalamind OneDrive Sync",
  description: "OneDrive continuous sync dashboard"
};

export default function RootLayout({
  children
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
