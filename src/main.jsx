import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./theme/fonts.css";
import "./theme/tokens.css";
import "./index.css";
import App from "./app/App.jsx";
import ErrorBoundary from "./components/ErrorBoundary.jsx";

createRoot(document.getElementById("root")).render(
  <StrictMode>
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </StrictMode>,
);
