// AuthContext -- the single public auth surface for all page components.
// Pages call useAuth().login / useAuth().register / useAuth().logout.
import { createContext, useContext, useEffect, useState } from "react"
import { getToken } from "@/lib/api"
import { login as loginApi, register as registerApi, me, logout as logoutApi } from "@/lib/auth"

const AuthContext = createContext(null)

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null)
  const [loading, setLoading] = useState(true)

  // On mount: restore session only when a token exists in localStorage.
  // Empty dep array means this runs exactly once -- no re-render loop.
  useEffect(() => {
    const token = getToken()
    if (token) {
      me()
        .then(setUser)
        .catch(() => {
          // api client already cleared the token on 401
          setUser(null)
        })
        .finally(() => setLoading(false))
    } else {
      setLoading(false)
    }
  }, [])

  /**
   * THE login entry point for page components.
   * Accepts { email, password } -- handles API call, token storage,
   * and /me fetch in one atomic operation.
   */
  const login = async (credentials) => {
    const result = await loginApi(credentials)
    if (!result || !result.access_token) {
      throw new Error("Login failed: invalid credentials or server response")
    }
    const userProfile = await me()
    setUser(userProfile)
    return userProfile
  }

  /**
   * THE register entry point for page components.
   * Accepts { email, password, name? } -- same atomic flow as login.
   */
  const register = async (credentials) => {
    const result = await registerApi(credentials)
    if (!result || !result.access_token) {
      throw new Error("Registration failed: server did not return a token")
    }
    const userProfile = await me()
    setUser(userProfile)
    return userProfile
  }

  const logout = () => {
    logoutApi()
    setUser(null)
  }

  return (
    <AuthContext.Provider value={{ user, loading, login, register, logout }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth() {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error("useAuth must be used within AuthProvider")
  return ctx
}
