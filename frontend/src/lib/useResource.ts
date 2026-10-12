// Fetch-and-hold. While a refetch is in flight the previous data stays on
// screen (dimmed by the caller via `refreshing`), so nothing jumps or flashes.
import { useCallback, useEffect, useRef, useState } from "react";
import { EngineError } from "./api";

export interface Resource<T> {
  data: T | undefined;
  error: EngineError | undefined;
  loading: boolean; // first load, nothing to show yet
  refreshing: boolean; // a newer copy is on its way
  reload: () => void;
}

export function useResource<T>(
  key: string | null,
  fetcher: (signal: AbortSignal) => Promise<T>,
  { refreshMs }: { refreshMs?: number } = {},
): Resource<T> {
  const [data, setData] = useState<T>();
  const [error, setError] = useState<EngineError>();
  const [inFlight, setInFlight] = useState(false);
  const [tick, setTick] = useState(0);
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  useEffect(() => {
    if (key === null) return;
    const ctl = new AbortController();
    setInFlight(true);
    fetcherRef
      .current(ctl.signal)
      .then((d) => {
        setData(d);
        setError(undefined);
      })
      .catch((e: unknown) => {
        if ((e as Error).name === "AbortError") return;
        setError(e instanceof EngineError ? e : new EngineError(String(e), -1));
      })
      .finally(() => {
        if (!ctl.signal.aborted) setInFlight(false);
      });
    return () => ctl.abort();
  }, [key, tick]);

  // A key change means a different thing is being looked at: drop the old one.
  const lastKey = useRef(key);
  useEffect(() => {
    if (lastKey.current !== key) {
      lastKey.current = key;
      setData(undefined);
      setError(undefined);
    }
  }, [key]);

  useEffect(() => {
    if (!refreshMs || key === null) return;
    const id = window.setInterval(() => {
      if (document.visibilityState === "visible") setTick((t) => t + 1);
    }, refreshMs);
    return () => window.clearInterval(id);
  }, [refreshMs, key]);

  const reload = useCallback(() => setTick((t) => t + 1), []);
  return { data, error, loading: inFlight && data === undefined, refreshing: inFlight && data !== undefined, reload };
}
