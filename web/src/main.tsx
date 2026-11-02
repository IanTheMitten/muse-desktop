import "./token";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import "@fontsource-variable/figtree";
import "@fontsource-variable/newsreader";
import "./styles/tokens.css";
import "./styles/base.css";
import { App } from "./App";
import { CrashScreen } from "./components/CrashScreen";
import { MiniWindow } from "./views/MiniWindow";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <CrashScreen>
      {location.pathname === "/mini" ? <MiniWindow /> : <App />}
    </CrashScreen>
  </StrictMode>,
);
