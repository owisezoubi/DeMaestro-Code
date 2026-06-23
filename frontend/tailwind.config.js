/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        // CSS-var-based design tokens (support opacity modifiers via /alpha)
        accent:           'rgb(var(--color-accent) / <alpha-value>)',
        'accent-secondary': 'rgb(var(--color-accent-secondary) / <alpha-value>)',
        'accent-fg':      'rgb(var(--color-accent-fg) / <alpha-value>)',
        'surface-page':   'rgb(var(--color-surface-page) / <alpha-value>)',
        'surface-panel':  'rgb(var(--color-surface-panel) / <alpha-value>)',
        'surface-border': 'rgb(var(--color-surface-border) / <alpha-value>)',
        'text-default':   'rgb(var(--color-text-default) / <alpha-value>)',
        'text-muted':     'rgb(var(--color-text-muted) / <alpha-value>)',
        success:          'rgb(var(--color-success) / <alpha-value>)',
        error:            'rgb(var(--color-error) / <alpha-value>)',
        warning:          'rgb(var(--color-warning) / <alpha-value>)',
        // Legacy primary scale (keep for existing .btn-primary, .card, etc.)
        primary: {
          50:  '#F2F6FB',
          100: '#DBE5F1',
          200: '#B8CCE2',
          300: '#8BAAD0',
          400: '#5278A8',
          500: '#34588A',
          600: '#1F3A68',
          700: '#172C50',
          800: '#0F1E37',
          900: '#0A1428',
        },
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
      },
    },
  },
  plugins: [],
}
