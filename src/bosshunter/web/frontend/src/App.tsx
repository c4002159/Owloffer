import { BrowserRouter, Routes, Route, Navigate, useSearchParams } from 'react-router-dom'
import { Sidebar } from './components/layout/Sidebar'
import { Header } from './components/layout/Header'
import DashboardPage from './pages/DashboardPage'
import ConfigPage from './pages/ConfigPage'
import { OnboardingTour } from './components/onboarding/OnboardingTour'
import { StarPrompt } from './components/layout/StarPrompt'
import { KeywordAdvicePopup } from './components/keywords/KeywordAdvice'
import { PlatformHoldPopup } from './components/dashboard/PlatformHoldPopup'

/** The settings page; which group it shows lives in the address (?section=), so the sidebar can switch it. */
function ConfigRoute() {
  const [params, setParams] = useSearchParams()
  return <ConfigPage category={params.get('section') || undefined} onCategoryChange={key => setParams({ section: key }, { replace: true })} />
}

function JobsPage() {
  return <DashboardPage view="jobs" />
}

function MonitorPage() {
  return <DashboardPage view="monitor" />
}

export default function App() {
  return (
    <BrowserRouter>
      <div className="flex h-screen overflow-hidden bg-background text-foreground">
        <Sidebar />
        <div className="min-w-0 flex-1 flex flex-col overflow-hidden">
          <Header />
          <main className="min-w-0 flex-1 overflow-y-auto p-3 md:p-6">
            <Routes>
              <Route path="/" element={<DashboardPage />} />
              <Route path="/jobs" element={<JobsPage />} />
              <Route path="/monitor" element={<MonitorPage />} />
              <Route path="/config" element={<ConfigRoute />} />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
          </main>
        </div>
        <OnboardingTour />
        <StarPrompt />
        <KeywordAdvicePopup />
        <PlatformHoldPopup />
      </div>
    </BrowserRouter>
  )
}
