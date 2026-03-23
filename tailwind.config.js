/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        'bloomberg-amber': '#ff8c00',
        'bloomberg-red': '#ff3333',
        'bloomberg-green': '#00ff00',
      }
    },
  },
  plugins: [],
}