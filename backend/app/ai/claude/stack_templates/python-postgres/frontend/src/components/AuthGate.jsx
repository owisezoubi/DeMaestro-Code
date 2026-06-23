import { Navigate } from "react-router-dom";
import { useAuth } from "@/contexts/AuthContext";

/**
 * Landing-route gate for auth-required apps.
 * - While AuthContext is resolving /me, show a thin splash.
 * - If user is authed, redirect to the primary in-app page.
 * - Otherwise redirect to /login.
 *
 * The redirect target defaults to /dashboard; override per-app
 * by editing AUTHED_LANDING below.
 */
const AUTHED_LANDING = "/dashboard";
const PUBLIC_LANDING = "/login";

export default function AuthGate() {
  const { user, loading } = useAuth();
  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center text-text-muted text-sm">
        Loading...
      </div>
    );
  }
  return (
    <Navigate
      to={user ? AUTHED_LANDING : PUBLIC_LANDING}
      replace
    />
  );
}
