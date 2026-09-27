import { useEffect, useMemo, useState } from 'react'
import { Navigation } from 'lucide-react'
import RouteMap from './RouteMap'
import { googleMapsLink, planTrip, suggestedRoute } from '../services/routing'
import type { Point, RoutePlan } from '../services/routing'
import type { CalendarEvent } from '../services/calendarApi'

const time = (value: string) => new Date(value).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
const tone: Record<string, string> = {
  safe: 'bg-emerald-50 text-emerald-900',
  reroute: 'bg-amber-50 text-amber-900',
  reroute_caution: 'bg-amber-50 text-amber-900',
  no_alternative: 'bg-red-50 text-red-800',
}

/** The trip DryRoute planned for one class: home -> its parking lot, the flood-safer route when rerouting.
 * Planned again whenever `conditions` change (demo storm, new home), so it always matches the leave-by and alerts. */
export default function EventTrip({ event, conditions, onStart }: { event: CalendarEvent; conditions: string; onStart: (label: string) => void }) {
  const [result, setResult] = useState<{ key: string; plan: RoutePlan | null; error: string } | null>(null)
  const key = `${JSON.stringify(event.route_query)}|${conditions}`

  useEffect(() => {
    if (!event.route_query) return
    const controller = new AbortController()
    planTrip(event.route_query, controller.signal)
      .then(plan => { if (!controller.signal.aborted) setResult({ key, plan, error: '' }) })
      .catch(cause => { if (!controller.signal.aborted) setResult({ key, plan: null, error: cause instanceof Error ? cause.message : 'Could not plan this trip.' }) })
    return () => controller.abort()
  }, [key, event.route_query])

  const plan = result?.key === key ? result.plan : null
  const route = plan ? suggestedRoute(plan) : null
  const points = useMemo<Point[]>(() => {
    const query = event.route_query
    if (!query || !route) return []
    const [lon, lat] = route.geometry.coordinates[route.geometry.coordinates.length - 1]
    return [{ lat: Number(query.from_lat), lon: Number(query.from_lon), label: 'Home' },
      { lat, lon, label: plan?.parking?.planned.name || event.location || 'Class' }]
  }, [event.route_query, event.location, route, plan])

  return <section aria-labelledby="trip-heading" className="mt-4 rounded-3xl border border-[#dce7ee] bg-white p-4">
    <p className="text-[10px] font-bold tracking-widest text-slate-500 uppercase">Your trip</p>
    <h2 id="trip-heading" className="mt-1 text-lg leading-tight font-bold">{event.event_name || 'Untitled event'}</h2>
    {!plan && !result?.error && <p role="status" className="mt-3 text-sm text-slate-600">Planning your route...</p>}
    {result?.key === key && result.error && <p role="alert" className="mt-3 rounded-xl bg-amber-50 p-3 text-sm text-amber-900">{result.error}</p>}
    {plan && route && <>
      <p className="mt-2 text-sm">Leave by <strong>{time(route.depart_at)}</strong> · {Math.round(route.eta_minutes)} min drive{plan.parking ? ` · park at ${plan.parking.planned.name}` : ''}</p>
      <p className={`mt-3 rounded-xl p-3 text-xs leading-5 ${tone[plan.recommendation.action] || 'bg-sky-50'}`}>{plan.recommendation.message}</p>
      <div className="mt-3"><RouteMap points={points} plans={[plan]} /></div>
      <p className="mt-2 text-xs text-slate-600">{route === plan.usual ? 'Blue: your route.' : 'Blue: flood-safer route. Dashed gray: usual route.'}</p>
      <a href={googleMapsLink(points, [plan])} target="_blank" rel="noopener noreferrer" onClick={() => onStart(event.event_name || 'your class')} className="mt-3 flex min-h-11 items-center justify-center gap-2 rounded-full bg-route-gold px-3 text-center text-sm font-bold text-route-navy">
        <Navigation size={16} aria-hidden="true" />Open in Google Maps
      </a>
      <p className="mt-2 text-[10px] leading-4 text-slate-500">Google Maps follows the {route === plan.usual ? 'route' : 'flood-safer route'} shown here.{route.eta_source === 'free_flow' ? ' Drive time uses speed limits, not live traffic.' : ''}</p>
    </>}
  </section>
}
