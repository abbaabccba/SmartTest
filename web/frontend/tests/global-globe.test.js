// @vitest-environment jsdom
import { expect, it, vi } from 'vitest'
import { geoDistance } from 'd3-geo'
import { createGlobalGlobe, globalLocations, selectGlobeConnections } from '../src/global-globe.js'

it('connects each Chinese hub to one random non-coincident customer, without inventing assignments', () => {
  const connections = selectGlobeConnections(() => 0)
  expect(connections.map(route => route.from.name)).toEqual(['Beijing', 'Shanghai', 'Shenzhen'])
  expect(connections).toHaveLength(3)
  for (const route of connections) {
    expect(route.to.kind).toBe('customer')
    expect(geoDistance(route.from.coordinates, route.to.coordinates)).toBeGreaterThan(0)
    expect(route.interpolate(0)[0]).toBeCloseTo(route.from.coordinates[0])
    expect(route.interpolate(0)[1]).toBeCloseTo(route.from.coordinates[1])
    expect(route.interpolate(1)[0]).toBeCloseTo(route.to.coordinates[0])
    expect(route.interpolate(1)[1]).toBeCloseTo(route.to.coordinates[1])
  }
  expect(selectGlobeConnections(() => .99).map(route => route.to.name)).not.toEqual(connections.map(route => route.to.name))
})

it('hides rear hemisphere markers, rotates the geography and bounds visible labels', () => {
  const gradient = { addColorStop() {} }
  const points = []
  const labels = []
  const context = { save() {}, restore() {}, beginPath() {}, clip() {}, fill() {}, stroke() {}, moveTo() {}, lineTo() {}, closePath() {},
    arc: vi.fn((...args) => points.push(args)), createRadialGradient: () => gradient,
    measureText: text => ({ width: text.length * 5 }), fillText: text => labels.push(text) }
  const draw = createGlobalGlobe(context)
  draw(900, 430, 0, false)
  const front = globalLocations.filter(location => geoDistance(location.coordinates, [110, 18]) < Math.PI / 2)
  // Administrative centers stay present in addition to the office/customer points.
  expect(points.length).toBeGreaterThan(front.length + 500)
  expect(labels).toContain('Asia')
  expect(labels).not.toContain('North America')
  const initial = [...points]
  points.length = labels.length = 0
  draw(900, 430, 90000, true)
  expect(points).not.toEqual(initial)
  expect(labels).toContain('North America')
  expect(labels).not.toContain('Asia')
  points.length = labels.length = 0
  draw(140, 42, 100000, false)
  expect(labels).toHaveLength(0)
})

it('keeps login labels within the visible sphere and capital markers present', () => {
  const arcs = []
  const labels = []
  const context = { save() {}, restore() {}, beginPath() {}, clip() {}, fill() {}, stroke() {}, moveTo() {}, lineTo() {}, closePath() {},
    arc: (...args) => arcs.push(args), createRadialGradient: () => ({ addColorStop() {} }),
    measureText: text => ({ width: text.length * 5 }), fillText: (text, x, y) => labels.push({ text, x, y }) }
  createGlobalGlobe(context, { framing: 'login' })(900, 800, 0, false)
  const [x, y, radius] = arcs[0]
  expect(arcs.length).toBeGreaterThan(500)
  expect(labels.some(label => label.text.includes('Beijing'))).toBe(true)
  expect(labels.some(label => label.text.includes('Washington'))).toBe(false)
  for (const label of labels) {
    // All corners must fit within the sphere, not merely the rectangular canvas.
    for (const px of [label.x, label.x + label.text.length * 5]) {
      for (const py of [label.y - 9, label.y]) expect(Math.hypot(px - x, py - y)).toBeLessThanOrEqual(radius)
    }
  }
})

it('crops the login sphere and changes latitude as it turns, without changing its framing', () => {
  const arcs = []
  const context = { save() {}, restore() {}, beginPath() {}, clip() {}, fill() {}, stroke() {}, moveTo() {}, lineTo() {}, closePath() {},
    arc: (...args) => arcs.push(args), createRadialGradient: () => ({ addColorStop() {} }),
    measureText: text => ({ width: text.length * 5 }), fillText() {} }
  const draw = createGlobalGlobe(context, { framing: 'login', random: () => 0 })
  draw(900, 800, 0, false)
  const sphere = arcs[0]
  expect(sphere[0] - sphere[2]).toBeLessThan(0)
  expect(sphere[1] + sphere[2]).toBeGreaterThan(800)
  const first = [...arcs]
  arcs.length = 0
  // One complete longitudinal turn: differences now come from the latitude sweep.
  draw(900, 800, 180000, false)
  expect(arcs[0]).toEqual(sphere)
  expect(arcs).not.toEqual(first)
})
