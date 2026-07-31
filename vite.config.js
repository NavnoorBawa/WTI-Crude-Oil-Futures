import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

const getSecureOrigin = (value, allowLocalHttp) => {
  if (!value) return null

  try {
    const url = new URL(value)
    if (url.protocol === 'https:') return url.origin
    if (
      allowLocalHttp &&
      url.protocol === 'http:' &&
      (url.hostname === '127.0.0.1' || url.hostname === 'localhost')
    ) {
      return url.origin
    }
  } catch {
    // Invalid build-time URLs are excluded from the browser's connection policy.
  }

  return null
}

const contentSecurityPolicyPlugin = (isDevelopment, env) => {
  const connectSources = new Set(["'self'", 'https://raw.githubusercontent.com'])
  for (const value of [env.VITE_API_BASE_URL, env.VITE_LIVE_PRICE_URL]) {
    const origin = getSecureOrigin(value, isDevelopment)
    if (origin) connectSources.add(origin)
  }

  if (isDevelopment) {
    connectSources.add('http://127.0.0.1:*')
    connectSources.add('http://localhost:*')
    connectSources.add('ws://127.0.0.1:*')
    connectSources.add('ws://localhost:*')
  }

  const directives = [
    "default-src 'self'",
    "base-uri 'self'",
    `connect-src ${[...connectSources].join(' ')}`,
    "font-src 'self'",
    "form-action 'none'",
    "frame-src 'none'",
    "img-src 'self' data:",
    "object-src 'none'",
    `script-src 'self'${isDevelopment ? " 'unsafe-inline'" : ''}`,
    "style-src 'self' 'unsafe-inline'",
  ]
  return {
    name: 'launch-content-security-policy',
    transformIndexHtml: {
      order: 'post',
      handler: () => [{
        tag: 'meta',
        attrs: {
          'http-equiv': 'Content-Security-Policy',
          content: directives.join('; '),
        },
        injectTo: 'head-prepend',
      }],
    },
  }
}

// https://vitejs.dev/config/
// base: GitHub Pages project sites serve from /<repo>/, so the CI build sets
// VITE_BASE_PATH=/WTI-Crude-Oil-Futures/. Local dev and custom-domain builds use '/'.
export default defineConfig(({ command, mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const isDevelopment = command === 'serve'

  return {
    base: env.VITE_BASE_PATH || '/',
    plugins: [react(), contentSecurityPolicyPlugin(isDevelopment, env)],
    server: {
      port: 3000,
      host: '127.0.0.1'
    },
    preview: {
      host: '127.0.0.1'
    },
    build: {
      outDir: 'dist'
    }
  }
})
