import type { NextConfig } from 'next';
const config: NextConfig = { output: 'standalone', outputFileTracingRoot: process.cwd(), distDir: process.env.NEXT_DIST_DIR || '.next', poweredByHeader: false, experimental: { middlewareClientMaxBodySize: '44mb' }, async rewrites() { const backend = process.env.API_BASE_URL || 'http://127.0.0.1:8000'; return [{ source: '/api/:path*', destination: `${backend}/api/:path*` }, { source: '/openapi.json', destination: `${backend}/openapi.json` }]; } };
export default config;
