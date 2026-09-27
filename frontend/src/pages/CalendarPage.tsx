import RoutePlanner from '../components/RoutePlanner'
import EventTrip from '../components/EventTrip'
import { useEffect, useRef, useState } from 'react'
import { ArrowLeft, CalendarDays, CloudRain, FlaskConical, RefreshCw, Route } from 'lucide-react'
import { disconnectCalendar, getCalendarConnection, getCalendarEvents, getNextEvent, getCalendarAlerts, getScenario, setScenario, addTestEvent, removeTestEvent, refreshAlerts, getProfile, updateProfile } from '../services/calendarApi'
import type { CalendarConnection, CalendarEvent, NextEvent, CalendarAlert, Scenario, Profile } from '../services/calendarApi'

import happyIcon from '../assets/Happy Icon.png'
import riskyIcon from '../assets/Risky Icon.png'
import neutralIcon from '../assets/Neutral Icon.png'
import onRouteIcon from '../assets/On Route Icon.png'

const date = (value: string) => new Date(value).toLocaleString([], { weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
const errorText = (error: unknown) => error instanceof Error ? error.message : 'Please try again.'
const alertTone: Record<CalendarAlert['severity'], string> = {
  critical: 'border-red-200 bg-red-50 text-red-900',
  warning: 'border-amber-200 bg-amber-50 text-amber-900',
  info: 'border-sky-200 bg-sky-50 text-sky-900',
}
// Demo homes off campus. Each one's storm route to campus crosses flood-prone roads (checked against the model).
const DEMO_HOMES = [
  { label: 'Brickell', lat: 25.7617, lon: -80.1918, note: 'long detour' },
  { label: 'Coral Gables', lat: 25.7500, lon: -80.2600, note: 'detour' },
  { label: 'Westchester', lat: 25.7470, lon: -80.3330, note: 'detour' },
  { label: 'Coral Terrace', lat: 25.7459, lon: -80.3045, note: 'detour' },
  { label: 'Shenandoah', lat: 25.75322, lon: -80.24144, note: 'short detour' },
  { label: 'Little Havana', lat: 25.7650, lon: -80.2200, note: 'no safer route: critical alert' },
]
const demoLabel = (label: string) => `${label} (demo home)`

export default function CalendarPage() {
  const [connection, setConnection] = useState<CalendarConnection | null>(null)
  const [events, setEvents] = useState<CalendarEvent[]>([])
  const [next, setNext] = useState<NextEvent | null>(null)
  const [alerts, setAlerts] = useState<CalendarAlert[]>([])
  const [error, setError] = useState('')
  const [planError, setPlanError] = useState('')
  const [loading, setLoading] = useState(true)
  const [checkingStatus, setCheckingStatus] = useState(true)
  const [mapStatus, setMapStatus] = useState<{ risky: boolean; stale: boolean } | null>(null)
  const [activeRoute, setActiveRoute] = useState<string | null>(null)
  const [refresh, setRefresh] = useState(0)
  const [name] = useState(() => { try { return sessionStorage.getItem('dryroute-user-name') || '' } catch { return '' } })
  const [scenario, setScenarioState] = useState<Scenario>('live')
  const [demoBusy, setDemoBusy] = useState(false)
  const [demoNote, setDemoNote] = useState('')
  const [profile, setProfile] = useState<Profile | null>(null)
  const [selectedEventId, setSelectedEventId] = useState<string | null>(null)
  const tripPanel = useRef<HTMLDivElement>(null)
  const hasTestEvent = events.some(event => event.event_id === 'dryroute_test_event')
  // The trip panel shows the class the student picked, else the next in-person class
  const tripEvent = events.find(event => event.event_id === selectedEventId && event.route_query)
    ?? events.find(event => event.event_id === next?.event_id && event.route_query)
  const home = profile?.home ? { lat: profile.home.lat, lon: profile.home.lon, label: profile.home.label || 'Home' } : null

  useEffect(() => {
    const controller = new AbortController()
    getScenario(controller.signal).then(weather => setScenarioState(weather.scenario)).catch(() => { /* Demo controls are optional. */ })
    getProfile(controller.signal).then(setProfile).catch(() => { /* The saved home only prefills the route planner. */ })
    return () => controller.abort()
  }, [refresh])

  function showRoute(eventId: string) {
    setSelectedEventId(eventId)
    tripPanel.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }

  // Demo controls: change the conditions, rebuild the alerts now, then reload the dashboard.
  async function runDemo(action: () => Promise<unknown>, note: string) {
    setDemoBusy(true)
    try { await action(); await refreshAlerts(); setDemoNote(note); setRefresh(value => value + 1) }
    catch (cause) { setDemoNote(errorText(cause)) }
    finally { setDemoBusy(false) }
  }
  const toggleStorm = () => {
    const nextScenario: Scenario = scenario === 'storm' ? 'live' : 'storm'
    void runDemo(async () => { await setScenario(nextScenario); setScenarioState(nextScenario) },
      nextScenario === 'storm' ? 'Storm on: flood risk now uses heavy rain.' : 'Storm off: back to the live forecast.')
  }
  // An off-campus home, so the storm demo shows flooding even when presenting from FIU.
  const chooseDemoHome = (index: number) => {
    const area = DEMO_HOMES[index]
    if (area) void runDemo(() => updateProfile({ home: { label: demoLabel(area.label), lat: area.lat, lon: area.lon } }),
      `Home set to ${area.label} (demo). Trips and alerts now start there.`)
  }
  const toggleTestEvent = () => void runDemo(hasTestEvent ? removeTestEvent : addTestEvent,
    hasTestEvent ? 'Test class removed.' : 'Test class added, starting in about 2 hours.')

  useEffect(() => {
    const controller = new AbortController()
    let running = false
    async function load() {
      if (running) return
      running = true
      setLoading(true)
      setCheckingStatus(true)
      setError('')
      setPlanError('')
      try {
        const status = await getCalendarConnection(controller.signal)
        if (controller.signal.aborted) return
        setConnection(status)
        if (!status.connected) { setEvents([]); setNext(null); setAlerts([]); return }
        // Schedule renders as soon as Google responds; routing can take longer.
        const schedule = getCalendarEvents(controller.signal).then(data => { if (!controller.signal.aborted) { setEvents(data); setLoading(false) } })
        const plan = Promise.all([getNextEvent(controller.signal), getCalendarAlerts(controller.signal)])
          .then(([event, warnings]) => { if (!controller.signal.aborted) { setNext(event); setAlerts(warnings) } })
          .catch(cause => { if (!controller.signal.aborted) { setNext(null); setAlerts([]); setPlanError(errorText(cause)) } })
        await Promise.allSettled([schedule.catch(cause => { if (!controller.signal.aborted) { setEvents([]); setError(errorText(cause)) } }), plan])
      } catch (cause) { if (!controller.signal.aborted) { setEvents([]); setError(errorText(cause)) } }
      finally { running = false; if (!controller.signal.aborted) { setLoading(false); setCheckingStatus(false) } }
    }
    void load()
    const timer = window.setInterval(() => { void load() }, 60000)
    return () => { controller.abort(); window.clearInterval(timer) }
  }, [refresh])

  async function disconnect() {
    try { await disconnectCalendar(); window.location.assign('/setup') }
    catch (cause) { setError(errorText(cause)) }
  }

  const hasRisk = mapStatus?.risky || alerts.some(alert => alert.active && (alert.severity === 'warning' || alert.severity === 'critical'))
    || Boolean(next?.trip?.compromised || next?.trip?.parking_hazardous)
  const status = hasRisk
    ? { image: riskyIcon, title: 'Heads up on your commute', detail: 'The latest available data reports a route or weather warning. Review the details below.', color: 'border-amber-200 bg-amber-50' }
    : activeRoute
      ? { image: onRouteIcon, title: 'On your way', detail: `Trip in progress: ${activeRoute}. Mark it complete when you arrive.`, color: 'border-sky-200 bg-sky-50' }
    : mapStatus && !mapStatus.stale
      ? { image: happyIcon, title: 'Your planned route looks good', detail: 'No high-risk roads were reported on the suggested route.', color: 'border-emerald-200 bg-emerald-50' }
    : checkingStatus || error || planError || !connection?.connected || mapStatus?.stale
      ? { image: neutralIcon, title: checkingStatus ? 'Checking your commute' : 'Waiting for reliable data', detail: checkingStatus ? 'Looking at your schedule and commute warnings.' : 'Connect your calendar or refresh to check current conditions.', color: 'border-[#dce7ee] bg-white' }
      : next?.trip
          ? { image: happyIcon, title: 'Your commute looks good', detail: 'No active warnings were reported in the latest commute check.', color: 'border-emerald-200 bg-emerald-50' }
          : { image: neutralIcon, title: 'Ready when you are', detail: 'No route is ready yet. Add an upcoming event with a recognized location and enable location in setup.', color: 'border-[#dce7ee] bg-white' }

  return <div className="min-h-svh bg-[#f4f9fc] sm:flex sm:flex-col sm:items-center sm:justify-center sm:gap-5 sm:bg-[radial-gradient(ellipse_at_top,#23435c,#091321_70%)] sm:p-10">
    <p className="hidden text-sm text-slate-300 sm:block">iPhone 17 / Preview</p>
    <div className="w-full sm:w-[422px] sm:rounded-[62px] sm:border sm:border-white/25 sm:bg-[#17191c] sm:p-[9px] sm:shadow-2xl">
      <main className="min-h-svh bg-[#f4f9fc] px-6 py-10 text-route-navy sm:h-[874px] sm:min-h-0 sm:overflow-y-auto sm:rounded-[52px]">
        <div aria-hidden="true" className="mx-auto mb-7 hidden h-[31px] w-[112px] rounded-full bg-black sm:block" />
        <header className="flex items-center justify-between"><a href="/setup" aria-label="Back to setup" className="rounded-full bg-white p-3"><ArrowLeft size={20} /></a><span className="text-xl font-bold"><span className="text-route-gold">Dry</span>Route</span><button onClick={() => setRefresh(value => value + 1)} aria-label="Refresh calendar" className="rounded-full bg-white p-3"><RefreshCw size={20} className={loading ? 'animate-spin' : ''} /></button></header>
        <h1 className="mt-7 text-3xl font-bold break-words">{name ? `Welcome, ${name}` : 'Your schedule'}</h1>
        <p className="mt-2 text-sm text-slate-600">Your next seven days, in your local time.</p>
        <section aria-live="polite" aria-atomic="true" className={`mt-6 flex items-center gap-3 rounded-[26px] border p-4 ${status.color}`}>
          <img src={status.image} alt="" className="h-32 w-28 shrink-0 object-contain" />
          <div className="min-w-0"><p className="text-[10px] font-bold tracking-widest text-slate-500 uppercase">{connection?.source === 'demo' ? 'Demo commute status' : 'Commute status'}</p><h2 className="mt-1 text-lg leading-tight font-bold">{status.title}</h2><p className="mt-2 text-xs leading-5 text-slate-600">{status.detail}</p></div>
        </section>
        {activeRoute && <div className="mt-3 rounded-2xl bg-white p-4"><p className="text-sm font-medium">Active trip: {activeRoute}</p><button type="button" onClick={() => setActiveRoute(null)} className="mt-3 min-h-11 w-full rounded-full bg-route-navy px-4 text-sm font-semibold text-white">End trip</button></div>}
        {!activeRoute && next?.trip && connection?.connected && <button type="button" disabled={checkingStatus || Boolean(error || planError)} onClick={() => setActiveRoute(next.event_name || 'Your next event')} className="mt-3 min-h-11 w-full rounded-full bg-route-navy px-4 text-sm font-semibold text-white disabled:opacity-50">Start trip to {next.event_name || 'your next event'}</button>}
        {connection?.connected && <div className="mt-4 flex items-center justify-between text-xs"><span className="rounded-full bg-white px-3 py-2">{connection.source === 'demo' ? 'Demo schedule' : connection.source === 'ics' ? 'Calendar imported' : 'Google Calendar connected'}</span><button onClick={disconnect} className="underline">Disconnect</button></div>}
        {connection?.connected && <section aria-labelledby="demo-heading" className="mt-4 rounded-[22px] border border-dashed border-[#cfdee8] bg-white p-4">
          <p id="demo-heading" className="text-[10px] font-bold tracking-widest text-slate-500 uppercase">Demo controls</p>
          <div className="mt-3 grid grid-cols-2 gap-2">
            <button type="button" onClick={toggleStorm} disabled={demoBusy} aria-pressed={scenario === 'storm'} className={`flex min-h-11 items-center justify-center gap-2 rounded-full px-3 text-xs font-semibold transition focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-route-navy disabled:opacity-50 ${scenario === 'storm' ? 'bg-route-navy text-white' : 'border border-[#cfdee8] bg-[#f4f9fc] hover:bg-sky-50'}`}><CloudRain size={16} aria-hidden="true" />{scenario === 'storm' ? 'Stop storm' : 'Simulate storm'}</button>
            <button type="button" onClick={toggleTestEvent} disabled={demoBusy} aria-pressed={hasTestEvent} className={`flex min-h-11 items-center justify-center gap-2 rounded-full px-3 text-xs font-semibold transition focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-route-navy disabled:opacity-50 ${hasTestEvent ? 'bg-route-navy text-white' : 'border border-[#cfdee8] bg-[#f4f9fc] hover:bg-sky-50'}`}><FlaskConical size={16} aria-hidden="true" />{hasTestEvent ? 'Remove test class' : 'Add test class'}</button>
          </div>
          <label className="mt-3 block text-xs font-semibold">Demo home area
            <select value={DEMO_HOMES.findIndex(area => profile?.home?.label === demoLabel(area.label))} onChange={event => chooseDemoHome(Number(event.target.value))} disabled={demoBusy} className="mt-1 min-h-11 w-full rounded-full border border-[#cfdee8] bg-[#f4f9fc] px-4 text-xs text-route-navy disabled:opacity-50">
              <option value={-1} disabled>{profile?.home ? `Current: ${profile.home.label || 'your location'}` : 'Choose an area'}</option>
              {DEMO_HOMES.map((area, index) => <option key={area.label} value={index}>{area.label} · {area.note}</option>)}
            </select>
          </label>
          <p role="status" className="mt-2 text-[10px] leading-4 text-slate-500">{demoNote || 'For an alert: pick a demo home, add the test class (about 2 hours from now), then simulate the storm.'}</p>
        </section>}
        {error && <p role="alert" className="mt-4 rounded-2xl bg-red-50 p-4 text-sm text-red-800">{error} <a href="/setup" className="underline">Connection settings</a></p>}
        {planError && <p role="alert" className="mt-4 rounded-2xl bg-amber-50 p-4 text-sm text-amber-900">Commute warnings could not be checked: {planError}</p>}
        {alerts.filter(alert => alert.active).map(alert => <article key={alert.alert_id} className={`mt-3 rounded-2xl border p-4 ${alertTone[alert.severity]}`}><h2 className="font-bold">{alert.title}</h2><p className="mt-1 text-sm">{alert.message}</p></article>)}
        {!loading && connection && !connection.connected && <section className="mt-6 rounded-3xl bg-white p-5"><CalendarDays /><h2 className="mt-3 font-bold">Connect your schedule</h2><p className="mt-2 text-sm text-slate-600">Connect Google Calendar to see your classes and work events, or choose the demo.</p><a href="/setup" className="mt-4 inline-block rounded-full bg-route-navy px-5 py-3 text-sm text-white">Set up Calendar</a></section>}
        {next && <section className="mt-6 rounded-3xl bg-route-navy p-5 text-white"><p className="text-xs text-sky-200">NEXT IN-PERSON EVENT</p><h2 className="mt-2 text-lg font-bold">{next.event_name || 'Untitled event'}</h2><p className="mt-2 text-sm">{date(next.start_time)}</p><p className="mt-2 text-sm">{next.recommended_departure ? `Leave by ${date(next.recommended_departure)}` : 'Set your location in setup and use a recognized FIU building in the event location to plan a commute.'}</p></section>}
        <div ref={tripPanel} className="scroll-mt-4">{tripEvent && <EventTrip event={tripEvent} conditions={`${scenario}|${profile?.home?.lat},${profile?.home?.lon}|${refresh}`} onStart={setActiveRoute} />}</div>
        <RoutePlanner events={events} home={home} onStart={setActiveRoute} onStatus={setMapStatus} />
        {connection?.connected && <section className="mt-7"><h2 className="text-lg font-bold">Upcoming events</h2><p className="mt-1 text-xs text-slate-500">Timed events · refreshes every minute</p>{loading && <p role="status" className="mt-4 text-sm">Loading your schedule...</p>}{!loading && !error && events.length === 0 && <p className="mt-4 rounded-2xl bg-white p-4 text-sm">{connection.source === 'ics' ? 'No timed events in the next seven days. Add a class to your calendar, then refresh (uploaded files: upload again).' : 'No timed events in the next seven days. Add a class or work event to your primary Google Calendar, then refresh.'}</p>}<div className="mt-4 space-y-3">{events.map(event => <article key={event.event_id} className="rounded-[22px] border border-[#dce7ee] bg-white p-4"><p className="text-xs font-medium text-[#92701d]">{date(event.start_time)}</p><h3 className="mt-2 font-bold">{event.event_name || 'Untitled event'}</h3><p className="mt-1 text-sm text-slate-600">{event.location || 'No location added'}</p>{event.route_query && <button type="button" onClick={() => showRoute(event.event_id)} aria-pressed={tripEvent?.event_id === event.event_id} className="mt-3 flex min-h-11 items-center gap-2 rounded-full border border-[#cfdee8] bg-[#f4f9fc] px-4 text-xs font-semibold hover:bg-sky-50 focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-route-navy"><Route size={16} aria-hidden="true" />{tripEvent?.event_id === event.event_id ? 'Showing route' : 'Show route'}</button>}</article>)}</div></section>}
      </main>
    </div>
  </div>
}
