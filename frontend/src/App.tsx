import CalendarPage from './pages/CalendarPage'
import LoginPage from './pages/LoginPage'
import GoogleCalendarPage from './pages/GoogleCalendarPage'
import UserNamePage from './pages/UserNamePage'

export default function App() {
  const pathname = window.location.pathname.replace(/\/+$/, '') || '/'

  // The existing Get Started link opens /signup. Keep welcome as the entry page.
  if (pathname === '/signup') {
    return <UserNamePage />
  }

  if (pathname === '/setup') {
    return <GoogleCalendarPage />
  }

  if (pathname === '/calendar' || pathname === '/dashboard') return <CalendarPage />

  return <LoginPage />
}
