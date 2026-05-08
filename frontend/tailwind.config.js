/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        primary: {
          50: '#F2F6FB',
          100: '#DBE5F1',
          200: '#B8CCE2',
          400: '#5278A8',
          600: '#1F3A68',
          700: '#172C50',
          800: '#0F1E37',
        },
        accent: {
          500: '#D97706',
          600: '#B26006',
        },
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
      },
    },
  },
  plugins: [],
}
