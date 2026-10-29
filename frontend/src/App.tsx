import { lazy, Suspense } from 'react'
import { Route, Routes } from 'react-router-dom'

import { FullScreenLoader, RequireAuth, RequirePermission } from './auth/guards'
import { AppLayout } from './components/AppLayout'
import { NotFound } from './components/States'

// One chunk per page, so the first load only fetches what the screen needs.
const LoginPage = lazy(() => import('./pages/LoginPage'))
const AcceptInvitePage = lazy(() => import('./pages/AcceptInvitePage'))
const DashboardPage = lazy(() => import('./pages/DashboardPage'))
const ProjectsPage = lazy(() => import('./pages/projects/ProjectsPage'))
const ProjectDetailPage = lazy(() => import('./pages/projects/ProjectDetailPage'))
const RequestsPage = lazy(() => import('./pages/requests/RequestsPage'))
const RequestFormPage = lazy(() => import('./pages/requests/RequestFormPage'))
const RequestDetailPage = lazy(() => import('./pages/requests/RequestDetailPage'))
const UsersPage = lazy(() => import('./pages/users/UsersPage'))
const AuditPage = lazy(() => import('./pages/AuditPage'))

export default function App() {
  return (
    <Suspense fallback={<FullScreenLoader />}>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/accept-invite" element={<AcceptInvitePage />} />
        <Route element={<RequireAuth />}>
          <Route element={<AppLayout />}>
            <Route index element={<DashboardPage />} />
            <Route path="requests" element={<RequestsPage />} />
            <Route path="requests/new" element={<RequirePermission permission="request.create"><RequestFormPage /></RequirePermission>} />
            <Route path="requests/:id" element={<RequestDetailPage />} />
            <Route path="requests/:id/resubmit" element={<RequirePermission permission="request.resubmit"><RequestFormPage /></RequirePermission>} />
            <Route path="projects" element={<ProjectsPage />} />
            <Route path="projects/:id" element={<ProjectDetailPage />} />
            <Route path="users" element={<RequirePermission permission="user.manage"><UsersPage /></RequirePermission>} />
            <Route path="audit" element={<RequirePermission permission="audit.view"><AuditPage /></RequirePermission>} />
            <Route path="*" element={<NotFound />} />
          </Route>
        </Route>
      </Routes>
    </Suspense>
  )
}
