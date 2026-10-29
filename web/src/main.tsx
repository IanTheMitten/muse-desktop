import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import "@fontsource-variable/figtree";
import "@fontsource-variable/newsreader";
import "./styles/tokens.css";
import "./styles/base.css";
import { App } from "./App";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
