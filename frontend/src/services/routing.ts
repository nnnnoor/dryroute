import { request } from './calendarApi'

export interface Point { lat: number; lon: number; label: string }
export interface RoadRoute {
  geometry: { type: 'LineString'; coordinates: [number, number][] }
  eta_minutes: number
  distance_m: number
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
}
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

export function googleMapsLink(points: Point[]): string {
  const coordinate = (point: Point) => `${point.lat},${point.lon}`
  const query = new URLSearchParams({ api: '1', origin: coordinate(points[0]), destination: coordinate(points[points.length - 1]), travelmode: 'driving' })
  if (points.length > 2) query.set('waypoints', points.slice(1, -1).map(coordinate).join('|'))
  return `https://www.google.com/maps/dir/?${query}`
}
