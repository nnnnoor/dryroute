import { request } from './calendarApi'

export interface Point { lat: number; lon: number; label: string }
export interface RoadRoute {
  geometry: { type: 'LineString'; coordinates: [number, number][] }
  eta_minutes: number
  distance_m: number
  depart_at: string
  arrive_at: string
  risk_label: string
  eta_source: string
}
export interface RoutePlan {
  usual: RoadRoute
  safe: RoadRoute
  recommendation: { action: string; message: string }
  coverage: { origin_in_area: boolean; destination_in_area: boolean; note: string | null }
  weather: { stale?: boolean }
  parking?: { planned: { name: string; center: [number, number] }; hazardous: boolean } | null
}
/** GET /routes parameters for a calendar trip (the backend's route_query: home -> lot, arrive_by). */
export type RouteQuery = Record<string, string | number>
export const suggestedRoute = (plan: RoutePlan) => ['reroute', 'reroute_caution'].includes(plan.recommendation.action) ? plan.safe : plan.usual

export async function planJourney(points: Point[], signal: AbortSignal): Promise<RoutePlan[]> {
  const plans: RoutePlan[] = []
  let departure = new Date().toISOString()
  for (let i = 1; i < points.length; i++) {
    const from = points[i - 1], to = points[i]
    const query = new URLSearchParams({ from_lat: String(from.lat), from_lon: String(from.lon), to_lat: String(to.lat), to_lon: String(to.lon), depart_at: departure })
    const plan = await request<RoutePlan>(`/routes?${query}`, { signal })
    if (!plan.coverage.origin_in_area || !plan.coverage.destination_in_area) throw new Error(`Leg ${i}: ${plan.coverage.note || 'Outside the mapped area.'} Choose points near mapped Miami roads.`)
    plans.push(plan)
    departure = suggestedRoute(plan).arrive_at
  }
  return plans
}

/** The trip DryRoute planned for a calendar event; plan again whenever conditions change (e.g. a storm). */
export const planTrip = (query: RouteQuery, signal: AbortSignal) =>
  request<RoutePlan>(`/routes?${new URLSearchParams(Object.entries(query).map(([key, value]) => [key, String(value)]))}`, { signal })

// Google Maps links take at most 3 waypoints on phones, so spend them where they matter.
const MAX_WAYPOINTS = 3

/** Where the suggested route leaves the usual one: the middle of each detour, longest detours first.
 * Sending these as waypoints makes Google Maps follow DryRoute's reroute instead of its own fastest path. */
function detourPoints(plan: RoutePlan, limit: number): Point[] {
  const route = suggestedRoute(plan)
  if (route === plan.usual || limit <= 0) return []
  const usual = new Set(plan.usual.geometry.coordinates.map(([lon, lat]) => `${lon},${lat}`))
  const runs: [number, number][][] = []
  let run: [number, number][] = []
  for (const coordinate of route.geometry.coordinates) {
    if (usual.has(`${coordinate[0]},${coordinate[1]}`)) { if (run.length) runs.push(run); run = [] }
    else run.push(coordinate)
  }
  if (run.length) runs.push(run)
  return runs.sort((a, b) => b.length - a.length).slice(0, limit)
    .map(detour => { const [lon, lat] = detour[Math.floor(detour.length / 2)]; return { lat, lon, label: 'Detour' } })
}

/** Google Maps directions through the points. With the planned legs, it also follows DryRoute's suggested
 * (flood-safer) route: stops come first, the remaining waypoints pin its detours. */
export function googleMapsLink(points: Point[], plans: RoutePlan[] = []): string {
  const coordinate = (point: Point) => `${point.lat},${point.lon}`
  const query = new URLSearchParams({ api: '1', origin: coordinate(points[0]), destination: coordinate(points[points.length - 1]), travelmode: 'driving' })
  const stops = points.slice(1, -1)
  let budget = MAX_WAYPOINTS - stops.length
  const waypoints: Point[] = []
  plans.forEach((plan, index) => {
    const detours = detourPoints(plan, Math.max(0, Math.ceil(budget / (plans.length - index))))
    budget -= detours.length
    waypoints.push(...detours)
    if (index < stops.length) waypoints.push(stops[index])
  })
  const via = plans.length ? waypoints : stops
  if (via.length) query.set('waypoints', via.map(coordinate).join('|'))
  return `https://www.google.com/maps/dir/?${query}`
}
