import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: "#121417",
        panel: "#f6f7f9",
        line: "#d9dee7",
      },
    },
  },
  plugins: [],
};

export default config;
