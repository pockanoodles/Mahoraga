import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import Shell from "./components/shell/Shell";
import ErrorBoundary from "./components/shell/ErrorBoundary";
import PerformancePage from "./pages/Performance";
import ObservatoryPage from "./pages/Observatory";
import PulsePage from "./pages/Pulse";
import ActivityPage from "./pages/Activity";
import ChatPage from "./pages/Chat";
import AgentsPage from "./pages/Agents";
import RoutingPage from "./pages/Routing";

// Wraps every page so a render-time throw can't white-screen the whole app.
function Boundary({ children }: { children: React.ReactNode }) {
  return <ErrorBoundary>{children}</ErrorBoundary>;
}

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<Shell />}>
          <Route index element={<Navigate to="/performance" replace />} />
          <Route path="/performance" element={<Boundary><PerformancePage /></Boundary>} />
          <Route path="/observatory" element={<Boundary><ObservatoryPage /></Boundary>} />
          <Route path="/pulse" element={<Boundary><PulsePage /></Boundary>} />
          <Route path="/activity" element={<Boundary><ActivityPage /></Boundary>} />
          <Route path="/chat" element={<Boundary><ChatPage /></Boundary>} />
          <Route path="/agents" element={<Boundary><AgentsPage /></Boundary>} />
          <Route path="/routing" element={<Boundary><RoutingPage /></Boundary>} />
          <Route path="*" element={<Navigate to="/performance" replace />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
