import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vitejs.dev/config/
// base: GitHub Pages project sites serve from /<repo>/, so the CI build sets
// VITE_BASE_PATH=/WTI-Crude-Oil-Futures/. Local dev and custom-domain builds use '/'.
export default defineConfig({
  base: process.env.VITE_BASE_PATH || '/',
  plugins: [react()],
  server: {
    port: 3000,
    host: true
  },
  build: {
    outDir: 'dist'
  }
})