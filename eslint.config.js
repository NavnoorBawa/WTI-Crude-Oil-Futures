import js from "@eslint/js";
import globals from "globals";
import reactHooks from "eslint-plugin-react-hooks";

// Flat config focused on REAL bugs, not style: undefined/unused variables and React Hooks
// violations (rules-of-hooks, exhaustive-deps). Mirrors the Python side, where ruff guards
// F/E9 only. Style is intentionally not enforced.
export default [
  { ignores: ["dist/**", "node_modules/**"] },
  {
    files: ["src/**/*.{js,jsx}"],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "module",
      globals: { ...globals.browser },
      parserOptions: { ecmaFeatures: { jsx: true } },
    },
    plugins: { "react-hooks": reactHooks },
    rules: {
      ...js.configs.recommended.rules,
      // Only the two CLASSIC, high-value Hooks rules — not the newest plugin's experimental
      // purity / set-state-in-effect rules, which flag intentional, benign patterns here (a live
      // Date.now() chart-timestamp fallback, deriving legend state in an effect) rather than bugs.
      // Same philosophy as the Python side (ruff --select F,E9): catch real bugs, not opinions.
      "react-hooks/rules-of-hooks": "error",
      "react-hooks/exhaustive-deps": "warn",
      // ESLint 10 tracks JSX references, so components used only in JSX count as used; no
      // capitalised-name escape hatch that would also hide genuinely unused imports.
      "no-unused-vars": ["warn", { varsIgnorePattern: "^_", argsIgnorePattern: "^_" }],
    },
  },
  {
    // Build tooling (vite.config.js, this file) runs in Node.
    files: ["*.config.js"],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "module",
      globals: { ...globals.node },
    },
    rules: {
      ...js.configs.recommended.rules,
      "no-unused-vars": ["warn", { varsIgnorePattern: "^_", argsIgnorePattern: "^_" }],
    },
  },
];
