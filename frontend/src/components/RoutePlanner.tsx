import { useEffect, useMemo, useRef, useState } from 'react'
import RouteMap from './RouteMap'
import { googleMapsLink, planJourney, suggestedRoute } from '../services/routing'
import type { Point, RoutePlan } from '../services/routing'
import type { CalendarEvent } from '../services/calendarApi'

const presets: Point[] = [
  { label: 'FIU main entrance', lat: 25.7610, lon: -80.3732 },
  { label: 'Brickell', lat: 25.7617, lon: -80.1918 },
  { label: 'Little Havana', lat: 25.7650, lon: -80.2200 },
  { label: 'Coral Gables', lat: 25.7500, lon: -80.2600 },
]
const field = 'mt-2 min-h-11 w-full rounded-xl border border-slate-300 bg-white px-3 text-sm'

export default function RoutePlanner({ events, onStart, onStatus }: { events: CalendarEvent[]; onStart: (label: string) => void; onStatus: (status: { risky: boolean; stale: boolean } | null) => void }) {
  const [origin, setOrigin] = useState<Point | null>(() => {
    try { const p = JSON.parse(sessionStorage.getItem('dryroute-onboarding') || '{}').coordinates; return p ? { lat: p.latitude, lon: p.longitude, label: 'Saved location' } : null } catch { return null }
  })
  const [destination, setDestination] = useState<Point | null>(null)
  const [stops, setStops] = useState<Point[]>([])
  const [mode, setMode] = useState<'origin' | 'destination' | 'stop'>('destination')
  const [coordinates, setCoordinates] = useState('')
  const [plans, setPlans] = useState<RoutePlan[]>([])
  const [plannedPoints, setPlannedPoints] = useState<Point[]>([])
  const [busy, setBusy] = useState(false)
  const [locating, setLocating] = useState(false)
  const [error, setError] = useState('')
  const controller = useRef<AbortController | null>(null)
  const mounted = useRef(true)
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; controller.current?.abort() } }, [])
  const choices = [...presets, ...events.filter(event => event.location_point).map(event => ({ ...event.location_point!, label: event.event_name || event.location || 'Calendar event' }))]
  const points = useMemo(() => [...(origin ? [origin] : []), ...stops, ...(destination ? [destination] : [])], [origin, stops, destination])
  function invalidate() { onStatus(null); controller.current?.abort(); setPlans([]); setPlannedPoints([]); setBusy(false); setError('') }
  function pick(point: Point) {
    invalidate()
    if (mode === 'origin') setOrigin(point)
    else if (mode === 'destination') setDestination(point)
    else if (stops.length < 3) setStops([...stops, point])
    else setError('You can add up to three stops.')
  }
  function addCoordinates() {
    const parts = coordinates.split(',').map(value => value.trim())
    const [lat, lon] = parts.map(Number)
    if (parts.length !== 2 || parts.some(value => !value) || !Number.isFinite(lat) || !Number.isFinite(lon) || Math.abs(lat) > 90 || Math.abs(lon) > 180) { setError('Enter latitude, longitude, for example 25.7617, -80.1918.'); return }
    pick({ lat, lon, label: coordinates })
    setCoordinates('')
  }
  function locate() {
    if (!navigator.geolocation) { setError('Location is unavailable in this browser. Set the start on the map.'); return }
    setLocating(true)
    navigator.geolocation.getCurrentPosition(position => {
      if (!mounted.current) return
      invalidate(); setOrigin({ lat: position.coords.latitude, lon: position.coords.longitude, label: 'Current location' }); setLocating(false)
    }, () => { if (mounted.current) { setLocating(false); setError('Could not get your location. Allow location access or select a start on the map.') } }, { enableHighAccuracy: true, timeout: 15000, maximumAge: 60000 })
  }
  async function calculate() {
    if (!origin || !destination) { setError('Choose a starting point and destination first.'); return }
    invalidate()
    const request = new AbortController()
    controller.current = request
    setBusy(true)
    try {
      const result = await planJourney(points, request.signal)
      if (!request.signal.aborted) { setPlans(result); setPlannedPoints(points); onStatus({ risky: result.some(plan => suggestedRoute(plan).risk_label === 'high' || plan.recommendation.action === 'no_alternative'), stale: result.some(plan => Boolean(plan.weather.stale)) }) }
    } catch (cause) { if (!request.signal.aborted) setError(cause instanceof Error ? cause.message : 'Could not plan this route.') }
    finally { if (!request.signal.aborted) setBusy(false) }
  }
  return <section className="mt-7 rounded-3xl border border-[#dce7ee] bg-white p-4">
    <h2 className="text-xl font-bold">Plan your route</h2>
    <p className="mt-2 mb-4 text-xs leading-5 text-slate-600">Choose a start, destination, and optional stops. DryRoute checks each leg against the available Miami road and flood data.</p>
    <RouteMap points={points} plans={plans} onPick={pick} />
    <p className="mt-2 text-xs text-slate-600">Blue: suggested route. Dashed gray: usual route.</p>
    <label className="mt-4 block text-sm font-semibold">Tap map to set<select className={field} value={mode} onChange={event => setMode(event.target.value as typeof mode)}><option value="origin">Starting point</option><option value="destination">Destination</option><option value="stop">Add a stop ({stops.length}/3)</option></select></label>
    <label className="mt-3 block text-sm font-semibold">Or choose a place<select className={field} value="" onChange={event => { if (event.target.value) pick(choices[Number(event.target.value)]) }}><option value="">Choose a location or calendar event</option>{choices.map((point, index) => <option value={index} key={`${point.label}-${index}`}>{point.label}</option>)}</select></label>
    <label className="mt-3 block text-sm font-semibold">Or enter latitude, longitude<input className={field} value={coordinates} onChange={event => setCoordinates(event.target.value)} placeholder="25.7617, -80.1918" /></label>
    <div className="mt-2 flex gap-2"><button type="button" onClick={addCoordinates} className="min-h-11 flex-1 rounded-xl bg-slate-100 px-2 text-xs font-semibold">Use coordinates</button><button type="button" onClick={locate} disabled={locating} className="min-h-11 flex-1 rounded-xl bg-sky-50 px-2 text-xs font-semibold">{locating ? 'Finding location...' : 'Start at my location'}</button></div>
    <ol className="mt-4 space-y-2 text-sm"><li><strong>Start:</strong> {origin?.label || 'Not selected'}</li>{stops.map((stop, index) => <li key={index} className="flex items-center justify-between gap-2"><span><strong>Stop {index + 1}:</strong> {stop.label}</span><button type="button" aria-label={`Remove stop ${index + 1}`} onClick={() => { invalidate(); setStops(stops.filter((_, i) => i !== index)) }} className="min-h-11 px-2 text-xs underline">Remove</button></li>)}<li><strong>Destination:</strong> {destination?.label || 'Not selected'}</li></ol>
    <button type="button" onClick={calculate} disabled={busy || locating} className="mt-4 min-h-11 w-full rounded-full bg-route-navy px-4 text-sm font-semibold text-white disabled:opacity-50">{busy ? 'Checking route legs...' : 'Find suggested route'}</button>
    {error && <p role="alert" className="mt-3 rounded-xl bg-amber-50 p-3 text-sm text-amber-900">{error}</p>}
    {plans.length > 0 && <div className="mt-4 space-y-3">
      <p className="font-bold">{Math.round(plans.reduce((total, plan) => total + suggestedRoute(plan).eta_minutes, 0))} min drive · {(plans.reduce((total, plan) => total + suggestedRoute(plan).distance_m, 0) / 1000).toFixed(1)} km</p>
      <p className="text-xs text-slate-600">Route data is checked when you press Find suggested route. Stops stay in your chosen order. Time spent at stops is not included. {plans.some(plan => suggestedRoute(plan).eta_source === 'free_flow') && 'Some travel times use speed limits, not live traffic.'}</p>
      {plans.map((plan, index) => <p key={index} className="rounded-xl bg-sky-50 p-3 text-xs leading-5"><strong>Leg {index + 1}:</strong> {plan.recommendation.message}{plan.weather.stale && ' Live risk data is stale; this uses the static flood-risk estimate.'}</p>)}
      <p className="text-xs leading-5 text-slate-600">Google Maps receives these stops and destination, then calculates its own directions. It may use different roads and does not receive DryRoute flood warnings.</p>
      <a href={googleMapsLink(plannedPoints)} target="_blank" rel="noopener noreferrer" onClick={() => onStart(plannedPoints[plannedPoints.length - 1].label)} className="flex min-h-11 items-center justify-center rounded-full bg-route-gold px-3 text-center text-sm font-bold text-route-navy">Start trip in Google Maps</a>
    </div>}
  </section>
}
