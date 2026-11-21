import { useCallback, useEffect, useState } from "react";
import { ApiError } from "../api/client";

export type ApiState<T> =
  | { status: "loading"; data: null; error: null }
  | { status: "error"; data: null; error: ApiError }
  | { status: "ready"; data: T; error: null };

type Result<T> = { key: string; depsKey: string; data: T; error: null } | { key: string; depsKey: string; data: null; error: ApiError };

/**
 * Load data from the API with loading, error and retry handling.
 *
 * `deps` must be primitives (they form the request key). `reload()` re-fetches; while a reload
 * for the same deps is in flight the previous data stays visible. Stale responses are ignored.
 */
export function useApi<T>(load: () => Promise<T>, deps: readonly (string | number | boolean | null)[] = []) {
  const [nonce, setNonce] = useState(0);
  const depsKey = JSON.stringify(deps);
  const key = `${depsKey}#${nonce}`;
  const [result, setResult] = useState<Result<T> | null>(null);

  useEffect(() => {
    let cancelled = false;
    load().then(
      (data) => {
        if (!cancelled) setResult({ key, depsKey, data, error: null });
      },
      (err: unknown) => {
        if (cancelled) return;
        const error =
          err instanceof ApiError ? err : new ApiError(0, "unknown", "Unexpected error while loading.");
        setResult({ key, depsKey, data: null, error });
      },
    );
    return () => {
      cancelled = true;
    };
    // `load` is intentionally excluded: the request key captures everything that matters.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  const reload = useCallback(() => setNonce((n) => n + 1), []);

  let state: ApiState<T>;
  const usable = result !== null && (result.key === key || (result.depsKey === depsKey && result.error === null));
  if (!usable) state = { status: "loading", data: null, error: null };
  else if (result.error !== null) state = { status: "error", data: null, error: result.error };
  else state = { status: "ready", data: result.data, error: null };
  return { ...state, reload };
}
