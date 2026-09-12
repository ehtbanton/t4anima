import type {Metadata} from 'next';
import {headers} from 'next/headers';
import './globals.css';
export async function generateMetadata():Promise<Metadata>{
 const h=await headers();const host=h.get('host')||'localhost:3000';const base=`${host.startsWith('localhost')||host.startsWith('127.0.0.1')?'http':'https'}://${host}`;
 return {title:'PGx Watch · Clinical agent workspace',description:'Evidence-led pharmacogenetics review for the Anima simulation.',openGraph:{title:'PGx Watch',description:'The right action. With the evidence.',images:[`${base}/og.png`]},twitter:{card:'summary_large_image',title:'PGx Watch',images:[`${base}/og.png`]}};
}
export default function RootLayout({children}:{children:React.ReactNode}){return <html lang="en"><body>{children}</body></html>}
