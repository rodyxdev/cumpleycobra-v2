import type { NextConfig } from "next";

// La app se renderiza en el cliente (token por sesión en localStorage, datos desde la API
// FastAPI), así que no usa Cache Components.
const nextConfig: NextConfig = {
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;
