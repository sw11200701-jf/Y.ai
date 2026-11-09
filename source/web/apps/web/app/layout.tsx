import type { Metadata } from 'next';
import { AntdRegistry } from '@ant-design/nextjs-registry';
import './globals.css';
import Providers from '@/components/providers';
import { PUBLIC_PROVENANCE_MARKER } from '@/generated/provenance-marker';
export const metadata: Metadata = { title: 'Y.ai', description: 'Y.ai 智能工作空间', icons:{icon:'/icon.svg'} };
export default function Layout({ children }: Readonly<{ children: React.ReactNode }>) { return <html lang="zh-CN"><head><meta name="application-build" content={PUBLIC_PROVENANCE_MARKER}/></head><body><AntdRegistry><Providers>{children}</Providers></AntdRegistry></body></html>; }
