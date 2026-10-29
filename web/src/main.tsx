import "./token";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import "@fontsource-variable/figtree";
import "@fontsource-variable/newsreader";
import "./styles/tokens.css";
import "./styles/base.css";
import { App } from "./App";
import { MiniWindow } from "./views/MiniWindow";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    {location.pathname === "/mini" ? <MiniWindow /> : <App />}
  </StrictMode>,
);
