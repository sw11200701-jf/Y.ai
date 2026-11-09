'use client';
import '@ant-design/v5-patch-for-react-19';
import { App, ConfigProvider } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import { designTokens, componentTokens } from '@/lib/design-tokens';
export default function Providers({ children }: { children: React.ReactNode }) {
  return <ConfigProvider locale={zhCN} theme={{ token: designTokens, components: componentTokens }}><App>{children}</App></ConfigProvider>;
}
