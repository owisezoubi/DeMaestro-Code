/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx,ts,tsx}'],
  theme: {
    extend: {
      colors: {
        surface: {
          page:   "rgb(var(--surface-page)   / <alpha-value>)",
          panel:  "rgb(var(--surface-panel)  / <alpha-value>)",
          border: "rgb(var(--surface-border) / <alpha-value>)",
        },
        text: {
          default: "rgb(var(--text-default) / <alpha-value>)",
          muted:   "rgb(var(--text-muted)   / <alpha-value>)",
        },
        accent: {
          DEFAULT: "rgb(var(--accent)    / <alpha-value>)",
          fg:      "rgb(var(--accent-fg) / <alpha-value>)",
        },
      },
    },
  },
  plugins: [],
}
