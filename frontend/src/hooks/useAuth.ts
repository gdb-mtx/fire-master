import { useState, useCallback } from "react";
import { useLocation, useNavigate } from "react-router";

/** Where to land after login: the page the visitor was sent away from (route
 *  guard state, or ?next= from an expired API session), never off-site. */
function returnPath(location: { state: unknown; search: string }): string {
  const fromState = (location.state as { from?: unknown } | null)?.from;
  const target = typeof fromState === "string" ? fromState : new URLSearchParams(location.search).get("next");
  if (!target || !target.startsWith("/") || target.startsWith("//") || target.startsWith("/login")) {
    return "/";
  }
  return target;
}

export function useAuth() {
  const [isAuthenticated, setIsAuthenticated] = useState(
    () => !!localStorage.getItem("token"),
  );
  const navigate = useNavigate();
  const location = useLocation();

  const login = useCallback(
    (token: string) => {
      localStorage.setItem("token", token);
      setIsAuthenticated(true);
      navigate(returnPath(location), { replace: true });
    },
    [navigate, location],
  );

  const logout = useCallback(() => {
    localStorage.removeItem("token");
    setIsAuthenticated(false);
    navigate("/login");
  }, [navigate]);

  return { isAuthenticated, login, logout };
}
