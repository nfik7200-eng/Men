import { useEffect, type ReactNode } from "react";
import { Loading } from "../components/ui";
import { usePageTitle } from "../lib/usePageTitle";
import { loadSession } from "./actions";
import SignIn from "./SignIn";
import { useAuth } from "./useAuth";

const RETRY_MS = 5000;

/**
 * Loads GET /auth/status when the app starts (this restores the session after a reload and
 * recovers the CSRF token) and keeps asking while the backend is unreachable.
 */
export function AuthProvider({ children }: { children: ReactNode }) {
  const { status } = useAuth();
  useEffect(() => {
    void loadSession();
  }, []);
  useEffect(() => {
    if (status !== "unavailable") return;
    const timer = setInterval(() => void loadSession(), RETRY_MS);
    return () => clearInterval(timer);
  }, [status]);
  return <>{children}</>;
}

function CheckingSignIn() {
  usePageTitle("Loading");
  return (
    <div className="auth-screen">
      <Loading label="Checking sign-in…" />
    </div>
  );
}

/**
 * Protected-route wrapper: when sign-in is required and nobody is signed in, only the sign-in
 * form renders and none of the application does. With sign-in disabled it renders the children
 * unchanged. While the backend is unreachable the shell renders (its own offline state applies).
 */
export function AuthGate({ children }: { children: ReactNode }) {
  const { status } = useAuth();
  if (status === "loading") return <CheckingSignIn />;
  if (status === "anonymous") return <SignIn />;
  return <>{children}</>;
}

/** Provider and gate together, for the application root. */
export function AuthRoot({ children }: { children: ReactNode }) {
  return (
    <AuthProvider>
      <AuthGate>{children}</AuthGate>
    </AuthProvider>
  );
}
