import { useEffect, useState, useCallback } from "react";

interface PollingState<T> {
  data: T | null;
  error: string | null;
  loading: boolean;
}

export function usePolling<T>(
  fetcher: (signal: AbortSignal) => Promise<T>,
  intervalMs: number
): PollingState<T> & { refresh: () => void } {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);

  const refresh = useCallback(() => setTick((n) => n + 1), []);

  useEffect(() => {
    const controller = new AbortController();
    let active = true;

    const run = async () => {
      try {
        const next = await fetcher(controller.signal);
        if (!active) return;
        setData(next);
        setError(null);
      } catch (err) {
        if (!active || (err as Error).name === "AbortError") return;
        setError((err as Error).message);
      } finally {
        if (active) setLoading(false);
      }
    };

    void run();
    const id = window.setInterval(run, intervalMs);
    return () => {
      active = false;
      controller.abort();
      window.clearInterval(id);
    };
    // fetcher is intentionally excluded — callers pass stable refs.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [intervalMs, tick]);

  return { data, error, loading, refresh };
}
