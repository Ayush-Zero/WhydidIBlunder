import { Route, Routes } from 'react-router-dom'
import { AppShell } from './components/AppShell'
import { AnalysisPage } from './pages/AnalysisPage'
import { HomePage } from './pages/HomePage'
import { NotFoundPage } from './pages/NotFoundPage'
import { PlayerPage } from './pages/PlayerPage'
import { ReportPage } from './pages/ReportPage'
import './App.css'

function App() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<HomePage />} />
        <Route path="player/:username" element={<PlayerPage />} />
        <Route
          path="player/:username/analysis/:jobId"
          element={<AnalysisPage />}
        />
        <Route path="player/:username/report" element={<ReportPage />} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  )
}

export default App
