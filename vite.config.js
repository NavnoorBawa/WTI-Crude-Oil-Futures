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

// The first screen needs data.json and both fonts, but the browser would only discover them after
// the main script has run (data.json) or the stylesheet has been parsed (fonts). Preload hints let
// it fetch them in parallel with the script. research.json and price.json are left alone: they are
// not needed for the first screen and would compete with the main script for bandwidth. The hint
// matches the app's own request (same URL, CORS mode, same-origin credentials), so it is reused.
const preloadPlugin = (staticDataMode) => {
  let base = '/'
  return {
    name: 'preload-first-screen',
    apply: 'build',
    configResolved(config) {
      base = config.base
    },
    transformIndexHtml: {
      order: 'post',
      handler: (_html, ctx) => {
        const fonts = Object.values(ctx.bundle ?? {})
          .filter((output) => output.type === 'asset' && output.fileName.endsWith('.woff2'))
          .map((output) => output.fileName)
          .sort()
        const tags = fonts.map((fileName) => ({
          tag: 'link',
          attrs: { rel: 'preload', as: 'font', type: 'font/woff2', href: `${base}${fileName}`, crossorigin: '' },
          injectTo: 'head',
        }))
        if (staticDataMode === 'true') {
          tags.unshift({
            tag: 'link',
            attrs: { rel: 'preload', as: 'fetch', href: `${base}data.json`, crossorigin: '' },
            injectTo: 'head',
          })
        }
        return tags
      },
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
  // The footer names the commit the bundle was built from: VITE_GIT_SHA when set, else the commit
  // GitHub Actions is building (GITHUB_SHA, a public value). Anything that is not a hex SHA is dropped.
  const gitSha = [env.VITE_GIT_SHA, process.env.GITHUB_SHA].find((value) => /^[0-9a-f]{7,40}$/i.test(value ?? '')) ?? ''

  return {
    base: env.VITE_BASE_PATH || '/',
    define: {
      'import.meta.env.VITE_STATIC_DATA': JSON.stringify(staticDataMode),
      'import.meta.env.VITE_GIT_SHA': JSON.stringify(gitSha),
    },
    plugins: [react(), contentSecurityPolicyPlugin(isDevelopment, env), preloadPlugin(staticDataMode)],
    server: {
      port: 3000,
      host: '127.0.0.1'
    },
    preview: {
      host: '127.0.0.1'
    },
    build: {
      outDir: 'dist',
      // Fonts must be served as files: the CSP allows font-src 'self', not data: URIs. Other small
      // assets keep Vite's default inlining.
      assetsInlineLimit: (filePath) => (/\.(woff2?|ttf|otf)$/i.test(filePath) ? false : undefined),
      rolldownOptions: {
        output: {
          // The charting library gets a chunk under its own name, so its size is traceable in the
          // build output (otherwise it is named after whichever small module shares it).
          codeSplitting: {
            groups: [{ name: 'lightweight-charts', test: /[\\/]node_modules[\\/](lightweight-charts|fancy-canvas)[\\/]/ }],
          },
        },
      },
    }
  }
})
