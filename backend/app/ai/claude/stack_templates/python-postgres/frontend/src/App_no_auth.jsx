// App.jsx — public / no-auth variant.
// No AuthProvider. No BareLayout (login/register pages absent).
// Every route is accessible to all visitors.
import { BrowserRouter, Routes, Route, Outlet } from "react-router-dom"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"

const queryClient = new QueryClient()

function Layout() {
  return (
    <div className="min-h-screen flex flex-col bg-surface-page text-text-default">
      <Outlet />
    </div>
  )
}

function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route element={<Layout />}>
            {/* generator inserts public page routes here */}
          </Route>
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  )
}

export default App
