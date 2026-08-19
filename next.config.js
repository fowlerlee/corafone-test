/** @type {import('next').NextConfig} */
const nextConfig = {
  // Server mode enabled for API routes
  // Do NOT add "output: 'export'" — it breaks /api/token
  images: {
    unoptimized: true,
  },
}

module.exports = nextConfig
