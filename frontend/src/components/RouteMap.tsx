import { useEffect, useRef } from 'react'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import type { Point, RoutePlan } from '../services/routing'
import { suggestedRoute } from '../services/routing'

export default function RouteMap({ points, plans, onPick }: { points: Point[]; plans: RoutePlan[]; onPick: (point: Point) => void }) {
  const container = useRef<HTMLDivElement>(null)
  const map = useRef<L.Map | null>(null)
  const layers = useRef<L.LayerGroup | null>(null)
  const pick = useRef(onPick)
  useEffect(() => { pick.current = onPick }, [onPick])
  useEffect(() => {
    if (!container.current) return
    const instance = L.map(container.current, { scrollWheelZoom: false }).setView([25.7563, -80.30], 12)
    map.current = instance
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', { attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>', maxZoom: 19 }).addTo(instance)
    layers.current = L.layerGroup().addTo(instance)
    instance.on('click', event => pick.current({ lat: event.latlng.lat, lon: event.latlng.lng, label: 'Selected on map' }))
    const resize = new ResizeObserver(() => instance.invalidateSize())
    resize.observe(container.current)
    return () => { resize.disconnect(); instance.remove(); map.current = null; layers.current = null }
  }, [])
  useEffect(() => {
    if (!map.current || !layers.current) return
    const group = layers.current
    group.clearLayers()
    const bounds = L.latLngBounds([])
    plans.forEach(plan => {
      const usual = plan.usual.geometry.coordinates.map(([lon, lat]) => [lat, lon] as L.LatLngTuple)
      const recommended = suggestedRoute(plan).geometry.coordinates.map(([lon, lat]) => [lat, lon] as L.LatLngTuple)
      L.polyline(usual, { color: '#94a3b8', weight: 6, dashArray: '6 8' }).addTo(group)
      L.polyline(recommended, { color: '#0284c7', weight: 5 }).addTo(group)
      recommended.forEach(point => bounds.extend(point))
    })
    points.forEach((point, index) => {
      const label = document.createElement('span')
      label.textContent = `${index === 0 ? 'Start' : index}: ${point.label}`
      L.circleMarker([point.lat, point.lon], { radius: 8, color: '#002347', fillColor: '#d5a52e', fillOpacity: 1 }).bindTooltip(label).addTo(group)
      bounds.extend([point.lat, point.lon])
    })
    if (bounds.isValid()) map.current.fitBounds(bounds, { padding: [25, 25], maxZoom: 15 })
  }, [points, plans])
  return <div ref={container} role="region" aria-label="Interactive route map. Use the location inputs below as an alternative to clicking the map." className="relative z-0 h-72 w-full rounded-2xl border border-slate-200" />
}
