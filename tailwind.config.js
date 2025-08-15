/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      screens: {
        'xs': '475px',
      },
      fontSize: {
        'xxs': ['10px', '12px'],
      },
      colors: {
        bloomberg: {
          amber: '#FFA500',
          'amber-light': '#FFB84D',
          black: '#000000',
          white: '#FFFFFF',
          red: '#FF433D',
          blue: '#0068FF',
          cyan: '#4AF6C3',
          orange: '#FB8B1E',
          green: '#00FF00',
          positive: '#00FF00',
          negative: '#FF433D',
          neutral: '#FFA500',
          volume: '#0068FF',
          alert: '#FFD700',
          'status-live': '#00FF88',
          'status-delayed': '#FFFF00',
          'status-error': '#FF4444',
          'status-warning': '#FFA500',
        },
      },
    },
  },
  plugins: [],
};
