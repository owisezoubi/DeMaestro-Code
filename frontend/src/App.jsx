import { Routes, Route, Navigate } from 'react-router-dom'

import Login from './pages/Login'
import Signup from './pages/Signup'
import Welcome from './pages/Welcome'
import Dashboard from './pages/Dashboard'
import NewProject from './pages/NewProject'
import ProjectApproval from './pages/ProjectApproval'
import ProjectChat from './pages/ProjectChat'
import ProjectDetail from './pages/ProjectDetail'
import ProjectGeneration from './pages/ProjectGeneration'
import ProfilePage from './pages/ProfilePage'
import AboutPage from './pages/AboutPage'
import ProtectedRoute from './components/ProtectedRoute'
import Navbar from './components/Navbar'
import ScrollToTop from './components/ScrollToTop'
import { ActiveGenerationBanner } from './components/ActiveGenerationBanner'

/**
 * Shared layout for every page that should show the Navbar.
 * Includes the active-generation banner (auto-hides on project pages).
 */
function Layout({ children }) {
  return (
    <>
      <Navbar />
      <div className="pt-[88px]">
        <ActiveGenerationBanner />
        {children}
      </div>
    </>
  )
}

export default function App() {
  return (
    <>
      <ScrollToTop />
      <Routes>
        {/* Public routes — no Navbar */}
        <Route path="/login" element={<Login />} />
        <Route path="/signup" element={<Signup />} />

        {/* Public route — with Navbar (no auth required) */}
        <Route path="/about" element={<Layout><AboutPage /></Layout>} />

        {/* Authenticated routes — Navbar + auth guard */}
        <Route
          path="/welcome"
          element={
            <ProtectedRoute>
              <Layout><Welcome /></Layout>
            </ProtectedRoute>
          }
        />
        <Route
          path="/dashboard"
          element={
            <ProtectedRoute>
              <Layout><Dashboard /></Layout>
            </ProtectedRoute>
          }
        />
        <Route
          path="/projects/new"
          element={
            <ProtectedRoute>
              <Layout><NewProject /></Layout>
            </ProtectedRoute>
          }
        />
        <Route
          path="/projects/:id"
          element={
            <ProtectedRoute>
              <Layout><ProjectChat /></Layout>
            </ProtectedRoute>
          }
        />
        <Route
          path="/projects/:id/approve"
          element={
            <ProtectedRoute>
              <Layout><ProjectApproval /></Layout>
            </ProtectedRoute>
          }
        />
        <Route
          path="/projects/:id/generation"
          element={
            <ProtectedRoute>
              <Layout><ProjectGeneration /></Layout>
            </ProtectedRoute>
          }
        />
        <Route
          path="/projects/:id/detail"
          element={
            <ProtectedRoute>
              <Layout><ProjectDetail /></Layout>
            </ProtectedRoute>
          }
        />
        <Route
          path="/profile"
          element={
            <ProtectedRoute>
              <Layout><ProfilePage /></Layout>
            </ProtectedRoute>
          }
        />

        <Route path="/" element={<Navigate to="/welcome" replace />} />
        <Route path="*" element={<Navigate to="/welcome" replace />} />
      </Routes>
    </>
  )
}
