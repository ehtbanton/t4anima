import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";
import { headers } from "next/headers";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

const baseMetadata: Metadata = {
  title: "Continuity — Every patient. The whole picture.",
  description: "An evidence-led GP workspace for patient history, review priorities and connected follow-up. Built with synthetic NHS-SIM data.",
};
export async function generateMetadata(): Promise<Metadata> {
  const h=await headers();
  const host=h.get('host')||'localhost:3000';
  const origin=`${host.startsWith('localhost')?'http':'https'}://${host}`;
  return {...baseMetadata,metadataBase:new URL(origin),openGraph:{title:'Continuity — Every patient. The whole picture.',description:baseMetadata.description||'',images:[{url:`${origin}/og.png`,width:1536,height:1024}]},twitter:{card:'summary_large_image',title:'Continuity',images:[`${origin}/og.png`]}};
}

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body
        className={`${geistSans.variable} ${geistMono.variable} antialiased`}
      >
        {children}
      </body>
    </html>
  );
}
