/** Google authorization and private calendar API calls use an HttpOnly backend cookie. */
export const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL?.trim() || 'http://localhost:8000').replace(/\/+$/, '')
export const GOOGLE_CALENDAR_URL = `${API_BASE_URL}/auth/google/start`
export interface CalendarConnection {
  connected: boolean
  source: 'google' | 'demo' | 'disconnected'
  google_configured: boolean
}
export interface CalendarEvent {
  event_id: string
  event_name: string | null
  start_time: string
  end_time: string
  location: string | null
  location_point: { lat: number; lon: number } | null
}
export interface NextEvent extends CalendarEvent {
  recommended_departure: string | null
  leave_in_minutes: number | null
  trip: { compromised: boolean; parking_hazardous: boolean; eta_minutes: number; parking_name: string | null; walk_minutes: number; parking_search_minutes: number } | null
}
export interface CalendarAlert {
  alert_id: string
  severity: 'info' | 'warning' | 'critical'
  title: string
  message: string
  read: boolean
  active: boolean
}
export interface ProfileUpdate {
  name?: string
  home?: { label: string; lat: number; lon: number }
}

export async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const timeout = AbortSignal.timeout(45000)
  const signal = init.signal ? AbortSignal.any([init.signal, timeout]) : timeout
  let response: Response
  try {
    response = await fetch(`${API_BASE_URL}${path}`, { ...init, signal, credentials: 'include', headers: { Accept: 'application/json', ...init.headers } })
  } catch (error) {
    if (init.signal?.aborted) throw error
    if (timeout.aborted) throw new Error('The request timed out. Please try again.', { cause: error })
    throw new Error('Could not reach the backend. Make sure it is running on port 8000.', { cause: error })
  }
  if (response.status === 204) return null as T
  let body: unknown
  try { body = await response.json() } catch { throw new Error(`Unexpected server response (${response.status}).`) }
  if (!response.ok) {
    const detail = body && typeof body === 'object' && 'detail' in body ? body.detail : null
    throw new Error(typeof detail === 'string' ? detail : `Request failed (${response.status}).`)
  }
  return body as T
}
export const getCalendarConnection = (signal?: AbortSignal) => request<CalendarConnection>('/calendar/connection', { signal })
export const connectDemoCalendar = () => request<CalendarConnection>('/calendar/demo', { method: 'POST' })
export const disconnectCalendar = () => request<CalendarConnection>('/calendar/connection', { method: 'DELETE' })
export const getCalendarEvents = (signal?: AbortSignal) => request<CalendarEvent[]>('/calendar/events?hours=168', { signal })
export const getNextEvent = (signal?: AbortSignal) => request<NextEvent | null>('/calendar/next-event', { signal })
export const getCalendarAlerts = (signal?: AbortSignal) => request<CalendarAlert[]>('/alerts', { signal })
export const updateProfile = (profile: ProfileUpdate) => request<unknown>('/me', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(profile) })
