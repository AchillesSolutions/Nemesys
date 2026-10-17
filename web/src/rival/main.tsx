import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import RivalApp from "./RivalApp";
import "../styles.css";
import "./rival.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <RivalApp />
  </StrictMode>,
);
