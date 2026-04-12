/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        bg: {
          primary: 'var(--bg-primary)',
          secondary: 'var(--bg-secondary)',
          card: 'var(--bg-card)',
        },
        edge: 'var(--border)',
        body: 'var(--text-primary)',
        muted: 'var(--text-muted)',
        subtle: 'var(--text-secondary)',
        green: 'var(--green)',
        red: 'var(--red)',
        yellow: 'var(--yellow)',
        blue: 'var(--blue)',
        accent: 'var(--accent)',
      },
      fontFamily: {
        mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
      },
    },
  },
  plugins: [],
}
