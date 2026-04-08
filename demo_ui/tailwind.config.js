export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        bg: "#f5f7fb",
        panel: "#ffffff",
        'panel-2': "#f8fafc",
        line: "#dde3ee",
        text: "#162033",
        muted: "#63708a",
        blue: {
          DEFAULT: "#2f6fed",
          soft: "#eaf1ff"
        },
        green: {
          DEFAULT: "#1b8f5a",
          soft: "#eaf8f1"
        },
        amber: {
          DEFAULT: "#b76e00",
          soft: "#fff4df"
        },
        purple: {
          DEFAULT: "#7a4ce0",
          soft: "#f3ecff"
        }
      },
      boxShadow: {
        DEFAULT: "0 10px 28px rgba(18, 33, 66, 0.08)",
      },
      borderRadius: {
        DEFAULT: "18px",
        sm: "12px"
      }
    },
  },
  plugins: [],
}