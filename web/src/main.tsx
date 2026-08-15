import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { ThemeProvider } from "@mui/material/styles";

import { App } from "./App";
import { somicsTheme } from "./theme";
import "@czi-sds/components/dist/variables.css";
import "./styles.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ThemeProvider theme={somicsTheme}>
      <App />
    </ThemeProvider>
  </StrictMode>,
);
