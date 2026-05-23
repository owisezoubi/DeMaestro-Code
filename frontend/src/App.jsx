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
import ProtectedRoute from './components/ProtectedRoute'

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/signup" element={<Signup />} />
      <Route
        path="/welcome"
        element={
          <ProtectedRoute>
            <Welcome />
          </ProtectedRoute>
        }
      />
      <Route
        path="/dashboard"
        element={
          <ProtectedRoute>
            <Dashboard />
          </ProtectedRoute>
        }
      />
      <Route
        path="/projects/new"
        element={
          <ProtectedRoute>
            <NewProject />
          </ProtectedRoute>
        }
      />
      <Route
        path="/projects/:id"
        element={
          <ProtectedRoute>
            <ProjectChat />
          </ProtectedRoute>
        }
      />
      <Route
        path="/projects/:id/approve"
        element={
          <ProtectedRoute>
            <ProjectApproval />
          </ProtectedRoute>
        }
      />
      <Route
        path="/projects/:id/generation"
        element={
          <ProtectedRoute>
            <ProjectGeneration />
          </ProtectedRoute>
        }
      />
      <Route
        path="/projects/:id/detail"
        element={
          <ProtectedRoute>
            <ProjectDetail />
          </ProtectedRoute>
        }
      />
      <Route path="/" element={<Navigate to="/welcome" replace />} />
      <Route path="*" element={<Navigate to="/welcome" replace />} />
    </Routes>
  )
}
