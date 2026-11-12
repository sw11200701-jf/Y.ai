import Link from 'next/link';

export default function NotFound() {
  return <main aria-labelledby="not-found-title" style={{ maxWidth: 560, margin: '12vh auto', padding: '0 24px' }}>
    <h1 id="not-found-title">页面不存在</h1>
    <p>请检查访问地址，或返回Yai 工作台。</p>
    <Link href="/">返回工作台</Link>
  </main>;
}
