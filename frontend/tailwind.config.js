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
      keyframes: {
        'pulse-slow': {
          '0%,100%': { opacity: '0.5' },
          '50%':     { opacity: '0.85' },
        },
        'float-y': {
          '0%,100%': { transform: 'translateY(0)' },
          '50%':     { transform: 'translateY(-14px)' },
        },
        'float-y-delayed': {
          '0%,100%': { transform: 'translateY(0)' },
          '50%':     { transform: 'translateY(-10px)' },
        },
        shimmer: {
          '0%':   { backgroundPosition: '-200% 0' },
          '100%': { backgroundPosition: '200% 0' },
        },
        'gradient': {
          '0%':   { backgroundPosition: '0% 50%' },
          '50%':  { backgroundPosition: '100% 50%' },
          '100%': { backgroundPosition: '0% 50%' },
        },
        'fade-slide-in': {
          '0%':   { opacity: '0', transform: 'translateY(6px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
        'fade-slide-out': {
          '0%':   { opacity: '1', transform: 'translateY(0)' },
          '100%': { opacity: '0', transform: 'translateY(-6px)' },
        },
        'dropdown': {
          '0%':   { opacity: '0', transform: 'translateY(-8px) scale(0.96)' },
          '100%': { opacity: '1', transform: 'translateY(0) scale(1)' },
        },
        'checklist-pass': {
          '0%':   { backgroundColor: 'rgba(16, 185, 129, 0.15)', transform: 'translateX(0)' },
          '50%':  { backgroundColor: 'rgba(16, 185, 129, 0.30)', transform: 'translateX(2px)' },
          '100%': { backgroundColor: 'transparent',              transform: 'translateX(0)' },
        },
        'shimmer-sweep': {
          '0%':   { transform: 'translateX(-100%)' },
          '100%': { transform: 'translateX(200%)' },
        },
        'fade-in': {
          '0%':   { opacity: '0', transform: 'translateY(4px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
        'card-enter': {
          '0%':   { opacity: '0', transform: 'scale(0.94) translateY(12px)' },
          '60%':  { opacity: '1', transform: 'scale(1.02) translateY(-2px)' },
          '100%': { opacity: '1', transform: 'scale(1) translateY(0)' },
        },
        'card-exit': {
          '0%':   { opacity: '1', transform: 'scale(1)' },
          '100%': { opacity: '0', transform: 'scale(0.9) translateY(-6px)' },
        },
        'ring-pulse': {
          '0%':   { transform: 'scale(1)', opacity: '1' },
          '80%':  { transform: 'scale(1.4)', opacity: '0' },
          '100%': { transform: 'scale(1.4)', opacity: '0' },
        },
        'just-created': {
          '0%':   { boxShadow: '0 0 0 0 rgba(124, 58, 237, 0.7)' },
          '60%':  { boxShadow: '0 0 0 20px rgba(124, 58, 237, 0)' },
          '100%': { boxShadow: '0 0 0 0 rgba(124, 58, 237, 0)' },
        },
        'question-in': {
          '0%':   { opacity: '0', transform: 'translateY(12px) scale(0.98)' },
          '100%': { opacity: '1', transform: 'translateY(0) scale(1)' },
        },
        'scale-in': {
          '0%':   { opacity: '0', transform: 'scale(0.93)' },
          '100%': { opacity: '1', transform: 'scale(1)' },
        },
        'slide-pulse': {
          '0%':   { transform: 'translateX(-100%)' },
          '100%': { transform: 'translateX(100%)' },
        },
        float: {
          '0%, 100%': { transform: 'translateY(0)' },
          '50%':      { transform: 'translateY(-6px)' },
        },
      },
      animation: {
        'pulse-slow':      'pulse-slow 6s ease-in-out infinite',
        'float-y':         'float-y 7s ease-in-out infinite',
        'float-y-delayed': 'float-y-delayed 9s ease-in-out infinite 1.5s',
        shimmer:           'shimmer 2s linear infinite',
        'shimmer-sweep':   'shimmer-sweep 1.8s ease-in-out infinite',
        'fade-in':         'fade-in 400ms ease-out both',
        gradient:          'gradient 4s ease infinite',
        'fade-slide-in':   'fade-slide-in 0.35s ease both',
        dropdown:          'dropdown 0.15s ease both',
        'checklist-pass':  'checklist-pass 800ms ease-out',
        'card-enter':      'card-enter 500ms cubic-bezier(0.34, 1.56, 0.64, 1) both',
        'card-exit':       'card-exit 320ms ease-in both',
        'ring-pulse':      'ring-pulse 900ms ease-out',
        'just-created':    'just-created 1.4s ease-out',
        'question-in':     'question-in 450ms cubic-bezier(0.34, 1.56, 0.64, 1) both',
        'scale-in':        'scale-in 200ms cubic-bezier(0.34, 1.56, 0.64, 1) both',
        'slide-pulse':     'slide-pulse 1.8s ease-in-out infinite',
        float:             'float 4s ease-in-out infinite',
      },
    },
  },
  plugins: [],
}
