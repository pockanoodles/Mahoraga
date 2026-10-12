import { BrowserRouter, Link, Route, Routes } from "react-router-dom";
import { EngineProvider } from "@/lib/engine";
import { AppShell } from "@/components/shell/AppShell";
import ArmsPage from "@/pages/Arms";
import RepositoriesPage from "@/pages/Repositories";
import RepositoryPage from "@/pages/Repository";
import RouterPage from "@/pages/Router";
import { RunPage, RunsPage } from "@/pages/Runs";
import SettingsPage from "@/pages/Settings";
import VerdictPage from "@/pages/Verdict";

export default function App() {
  return (
    <EngineProvider>
      <BrowserRouter>
        <Routes>
          <Route element={<AppShell />}>
            <Route index element={<VerdictPage />} />
            <Route path="repositories" element={<RepositoriesPage />} />
            <Route path="repositories/:id" element={<RepositoryPage />} />
            <Route path="runs" element={<RunsPage />} />
            <Route path="runs/:id" element={<RunPage />} />
            <Route path="arms" element={<ArmsPage />} />
            <Route path="router" element={<RouterPage />} />
            <Route path="settings" element={<SettingsPage />} />
            <Route
              path="*"
              element={
                <main className="page">
                  <h1 className="large-title">Nothing here</h1>
                  <p className="prose">
                    <Link className="link" to="/">
                      Back to the verdict
                    </Link>
                  </p>
                </main>
              }
            />
          </Route>
        </Routes>
      </BrowserRouter>
    </EngineProvider>
  );
}
