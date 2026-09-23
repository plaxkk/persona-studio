import React from "react";
import { createRoot } from "react-dom/client";
import { desktopMode } from "./desktop";
import { DesktopGate } from "./components/DesktopGate";
import App from "./App";
import "./styles.css";
createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    {desktopMode ? <DesktopGate /> : <App />}
  </React.StrictMode>,
);
