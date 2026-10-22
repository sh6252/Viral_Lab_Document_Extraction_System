import { createRoot } from "react-dom/client";
import App from "./App.tsx";
import AuthGate from "./AuthGate.tsx";
import { configureAPI } from "./api.ts";
import "./style.css";

const publicDemo = import.meta.env.VITE_PUBLIC_DEMO === "true";
configureAPI({ baseURL: import.meta.env.VITE_API_BASE_URL ?? "", publicDemo,
  onUnauthorized: () => window.dispatchEvent(new Event("demo-session-expired")) });
createRoot(document.getElementById("root")!).render(publicDemo ? <AuthGate>{controls => <App sessionControls={controls} />}</AuthGate> : <App />);
