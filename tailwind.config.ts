import type { Config } from 'tailwindcss'

const config: Config = {
  content: [
    './app/**/*.{js,ts,jsx,tsx,mdx}',
    './components/**/*.{js,ts,jsx,tsx,mdx}',
  ],
  theme: {
    extend: {
      colors: {
        background: '#0B0F19',
        surface: '#111726',
        'surface-2': '#1A2333',
        primary: '#3B82F6',
        accent: '#F59E0B',
        'text-primary': '#F8FAFC',
        'text-muted': '#94A3B8',
        border: '#1E293B',
        success: '#22C55E',
        error: '#EF4444',
      },
      fontFamily: {
        sans: ['var(--font-sans)', 'system-ui', 'sans-serif'],
      },
    },
  },
  plugins: [],
}

export default config
