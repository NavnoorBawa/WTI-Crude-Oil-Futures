import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./theme/fonts.css";
import "./theme/tokens.css";
import "./index.css";
import "./styles/chrome-foundation.css";
import "./styles/reading-surface.css";
import "./styles/verdict-lede.css";
import "./styles/verdict-tiles.css";
import "./styles/live-record.css";
import "./styles/forecast-figures.css";
import "./styles/model-comparison.css";
import "./styles/calibration-risk-cone.css";
import "./styles/reference-sections.css";
import "./styles/navigation.css";
import App from "./app/App.jsx";
import ErrorBoundary from "./components/ErrorBoundary.jsx";

createRoot(document.getElementById("root")).render(
  <StrictMode>
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </StrictMode>,
);
