import { Navigate, useLocation } from "react-router-dom";
import { useAuth } from "@/contexts/AuthContext";

/**
 * Wraps protected routes. Behavior:
 *  - If auth state is still loading (token in localStorage, /me
 *    in flight) -> render a thin loading splash so the page does
 *    not crash trying to read user.id.
 *  - If user is null after loading -> redirect to /login,
 *    preserving the attempted path in location.state.from so
 *    the login page can bounce back after success.
 *  - Otherwise -> render children.
 *
 * Usage:
 *   <Route path="/dashboard" element={
 *     <RequireAuth><DashboardPage /></RequireAuth>
 *   } />
 */
export default function RequireAuth({ children }) {
  const { user, loading } = useAuth();
  const location = useLocation();

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center text-text-muted text-sm">
        Loading...
      </div>
    );
  }
  if (!user) {
    return <Navigate to="/login" state={{ from: location }} replace />;
  }
  return children;
}
