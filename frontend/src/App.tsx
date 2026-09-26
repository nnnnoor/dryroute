import LoginPage from './pages/LoginPage'
import GoogleCalendarPage from './pages/GoogleCalendarPage'

export default function App() {
  const pathname = window.location.pathname.replace(/\/+$/, '') || '/'

  // The existing Get Started link opens /signup. Keep welcome as the entry page.
  if (pathname === '/signup') {
    return <GoogleCalendarPage />
  }

  return <LoginPage />
}
