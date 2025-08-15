import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    port: 4000,
    host: true, // Allow external connections
    strictPort: true, // Exit if port is already in use
    cors: true, // Enable CORS for all origins
    proxy: {
      // Proxy API calls to the Python server
      '/api': {
        target: 'http://127.0.0.1:9000',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, '')
      }
    }
  },
  build: {
    outDir: 'dist',
    sourcemap: true, // Enable source maps for debugging
    rollupOptions: {
      output: {
        manualChunks: {
          vendor: ['react', 'react-dom'],
          charts: ['chart.js', 'react-chartjs-2']
        }
      }
    }
  },
  optimizeDeps: {
    include: ['react', 'react-dom', 'chart.js', 'react-chartjs-2']
  },
  define: {
    // Define global constants for the complex ML system
    __DEV__: JSON.stringify(process.env.NODE_ENV === 'development'),
    __API_URL__: JSON.stringify(process.env.NODE_ENV === 'development' 
      ? 'http://127.0.0.1:9000' 
      : 'http://127.0.0.1:9000')
  }
})