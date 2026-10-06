import React from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "./style.css";
import { errorDetails, logStep } from "./logger";

logStep("app.starting");
const onError = (event: ErrorEvent) => logStep("app.error", { message: event.message, ...errorDetails(event.error) }, "error");
const onRejection = (event: PromiseRejectionEvent) => logStep("app.unhandled_rejection", errorDetails(event.reason), "error");
window.addEventListener("error", onError);
window.addEventListener("unhandledrejection", onRejection);
import.meta.hot?.dispose(() => {
  window.removeEventListener("error", onError);
  window.removeEventListener("unhandledrejection", onRejection);
});

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
logStep("app.render_requested");
