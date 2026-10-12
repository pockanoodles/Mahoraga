// What the app knows about the engine it is talking to. Polled, because the
// engine's one moving part the app cares about between page loads is whether
// Ollama is up.
import { createContext, useContext, type ReactNode } from "react";
import { api, type EngineError } from "./api";
import type { Engine } from "./types";
import { useResource } from "./useResource";

interface EngineState {
  engine: Engine | undefined;
  error: EngineError | undefined;
  reload: () => void;
}

const Ctx = createContext<EngineState>({ engine: undefined, error: undefined, reload: () => {} });

export function EngineProvider({ children }: { children: ReactNode }) {
  const r = useResource("engine", api.engine, { refreshMs: 10_000 });
  return <Ctx.Provider value={{ engine: r.data, error: r.error, reload: r.reload }}>{children}</Ctx.Provider>;
}

export const useEngine = () => useContext(Ctx);
