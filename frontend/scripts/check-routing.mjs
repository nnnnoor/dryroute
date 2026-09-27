import assert from 'node:assert/strict'
import { createServer } from 'vite'

const server = await createServer({ server: { middlewareMode: true }, appType: 'custom' })
const originalFetch = globalThis.fetch
try {
  const { googleMapsLink, planJourney, suggestedRoute } = await server.ssrLoadModule('/src/services/routing.ts')
  const points = [
    { lat: 25.7617, lon: -80.1918, label: 'Start' },
    { lat: 25.765, lon: -80.22, label: 'Stop' },
    { lat: 25.761, lon: -80.3732, label: 'Destination' },
  ]
  const url = new URL(googleMapsLink(points))
  assert.equal(url.searchParams.get('origin'), '25.7617,-80.1918')
  assert.equal(url.searchParams.get('waypoints'), '25.765,-80.22')
  assert.equal(url.searchParams.get('destination'), '25.761,-80.3732')
  assert.equal(new URL(googleMapsLink([points[0], points[2]])).searchParams.has('waypoints'), false)
  const plan = {
    usual: { arrive_at: '2026-09-27T12:10:00Z', eta_minutes: 10 },
    safe: { arrive_at: '2026-09-27T12:12:00Z', eta_minutes: 12 },
    recommendation: { action: 'reroute' },
    coverage: { origin_in_area: true, destination_in_area: true },
  }
  assert.equal(suggestedRoute(plan), plan.safe)
  assert.equal(suggestedRoute({ ...plan, recommendation: { action: 'safe' } }), plan.usual)
  const calls = []
  globalThis.fetch = async input => {
    calls.push(new URL(input))
    return new Response(JSON.stringify(plan), { status: 200 })
  }
  const results = await planJourney(points, new AbortController().signal)
  assert.equal(results.length, 2)
  assert.equal(calls[1].searchParams.get('from_lon'), '-80.22')
  assert.equal(calls[1].searchParams.get('depart_at'), plan.safe.arrive_at)
  globalThis.fetch = async () => new Response(JSON.stringify({ ...plan, coverage: { origin_in_area: false, destination_in_area: true, note: 'Start outside coverage.' } }))
  await assert.rejects(planJourney(points, new AbortController().signal), /Leg 1: Start outside coverage/)
  globalThis.fetch = async () => new Response(JSON.stringify({ detail: 'No route available.' }), { status: 400 })
  await assert.rejects(planJourney(points, new AbortController().signal), /No route available/)
  console.log('Routing checks passed: handoff, recommendation, leg order/timing, coverage and API errors.')
} finally {
  globalThis.fetch = originalFetch
  await server.close()
}
