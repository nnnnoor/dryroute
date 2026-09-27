/** Google authorization and private calendar API calls use an HttpOnly backend cookie. */
import type { RouteQuery } from './routing'

export const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL?.trim() || 'http://localhost:8000').replace(/\/+$/, '')
export const GOOGLE_CALENDAR_URL = `${API_BASE_URL}/auth/google/start`
export interface CalendarConnection {
  connected: boolean
  source: 'google' | 'demo' | 'ics' | 'disconnected'
  google_configured: boolean
}
export interface CalendarEvent {
  event_id: string
  event_name: string | null
  start_time: string
  end_time: string
  location: string | null
  location_point: { lat: number; lon: number } | null
  /** GET /routes parameters for this class's trip (saved home -> its lot, arriving in time); null if none. */
  route_query?: RouteQuery | null
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
/** Result of importing an iCal link or .ics file (docs/api-contracts.md, /calendar/ics/*). */
export interface CalendarImport extends CalendarConnection {
  events_next_7_days: number
  upcoming: { event_name: string | null; start_time: string; location: string | null; building_found: boolean }[]
}
export interface Profile {
  name: string | null
  home: { label: string | null; lat: number; lon: number } | null
  preferred_parking_id: string | null
  arrival_buffer_minutes: number
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
export const getProfile = (signal?: AbortSignal) => request<Profile>('/me', { signal })
export const updateProfile = (profile: ProfileUpdate) => request<unknown>('/me', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(profile) })
export const importCalendarLink = (url: string) => request<CalendarImport>('/calendar/ics/url', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ url }) })
// The file itself is the request body (not a form upload).
export const uploadCalendarFile = (file: File) => request<CalendarImport>('/calendar/ics/upload', { method: 'POST', headers: { 'Content-Type': 'text/calendar' }, body: file })
// Demo controls (docs/api-contracts.md, /demo/*): the storm switch is shared; the test class is this browser's only.
export type Scenario = 'live' | 'storm'
export const getScenario = (signal?: AbortSignal) => request<{ scenario: Scenario }>('/weather', { signal })
export const setScenario = (scenario: Scenario) => request<{ scenario: Scenario }>('/demo/scenario', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ scenario }) })
export const addTestEvent = () => request<{ event_name: string; start_time: string; location: string }>('/demo/test-event', { method: 'POST' })
export const removeTestEvent = () => request<{ removed: boolean }>('/demo/test-event', { method: 'DELETE' })
export const refreshAlerts = () => request<CalendarAlert[]>('/alerts/refresh', { method: 'POST' })
