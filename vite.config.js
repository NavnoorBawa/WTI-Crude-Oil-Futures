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

// A production bundle must never silently point at the local dev API (http://127.0.0.1:9000).
// Without VITE_API_BASE_URL, a build defaults to static-snapshot mode (the GitHub Pages setup);
// an explicit VITE_STATIC_DATA=false with no API base is a misconfiguration and fails the build.
const resolveStaticDataMode = (isBuild, env) => {
  const requested = env.VITE_STATIC_DATA
  if (requested === 'true') return 'true'
  if (!isBuild || env.VITE_API_BASE_URL) return requested ?? 'false'
  if (requested !== undefined && requested !== '') {
    throw new Error(
      `VITE_STATIC_DATA=${requested} with no VITE_API_BASE_URL would ship a bundle that polls ` +
      'http://127.0.0.1:9000. Set VITE_STATIC_DATA=true (GitHub Pages) or VITE_API_BASE_URL.'
    )
  }
  console.warn('[vite] VITE_STATIC_DATA is not set: building in static-snapshot mode (reads data.json).')
  return 'true'
}

// https://vitejs.dev/config/
// base: GitHub Pages project sites serve from /<repo>/, so the CI build sets
// VITE_BASE_PATH=/WTI-Crude-Oil-Futures/. Local dev and custom-domain builds use '/'.
export default defineConfig(({ command, mode }) => {
  // Only VITE_-prefixed variables are ever read here, so no other secret in the environment
  // can leak into the build config.
  const env = loadEnv(mode, process.cwd(), 'VITE_')
  const isDevelopment = command === 'serve'
  const staticDataMode = resolveStaticDataMode(command === 'build', env)

  return {
    base: env.VITE_BASE_PATH || '/',
    define: {
      'import.meta.env.VITE_STATIC_DATA': JSON.stringify(staticDataMode),
    },
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
