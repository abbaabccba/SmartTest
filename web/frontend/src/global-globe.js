import { geoDistance, geoGraticule10, geoInterpolate, geoOrthographic, geoPath } from 'd3-geo'
import boundaries from './geo/global-boundaries.json'
import detail from './geo/global-detail.json'

const geographyLabels = [
  ['North America', -105, 45], ['South America', -60, -15], ['Europe', 18, 53],
  ['Africa', 20, 5], ['Asia', 95, 43], ['Australia', 134, -25], ['Antarctica', 0, -78],
  ['Pacific Ocean', 160, 10], ['Pacific Ocean', -140, 5], ['Atlantic Ocean', -35, 20],
  ['Indian Ocean', 75, -20], ['Arctic Ocean', 0, 78], ['Southern Ocean', 90, -58],
]

// City centers, not building addresses; customer points represent headquarters only.
export const globalLocations = [
  ['Shanghai', 121.47, 31.23, '156'], ['Shenzhen', 114.06, 22.54, '156'],
  ['Beijing', 116.4, 39.9, '156'], ["Xi’an", 108.94, 34.34, '156'],
  ['Chengdu', 104.07, 30.67, '156'], ['Nanjing', 118.8, 32.06, '156'],
  ['Mountain View', -122.08, 37.39, 'US-06'], ['Bangalore', 77.59, 12.97, '356'],
  ['Seoul', 126.98, 37.57, '410'], ['Singapore', 103.82, 1.35, '702'],
  ['Tokyo', 139.69, 35.68, '392'], ['London', -.13, 51.51, '826'],
  ['Milan', 9.19, 45.46, '380'], ['Munich', 11.58, 48.14, '276'],
  ['Istanbul', 28.98, 41.01, '792'], ['Novi Sad', 19.83, 45.27, '688'],
  ['Paris', 2.35, 48.86, '250'], ['São Paulo', -46.63, -23.55, '076'],
  ['Hong Kong', 114.17, 22.32, '344'], ['Taipei', 121.57, 25.03, '158'],
].map(([name, longitude, latitude, region]) => ({ name, coordinates: [longitude, latitude], region, kind: 'office' }))
  .concat([
    ['Google · Mountain View', -122.08, 37.39, 'US-06'], ['Amazon · Seattle', -122.33, 47.61, 'US-53'],
    ['Walmart · Bentonville', -94.21, 36.37, 'US-05'], ['Deutsche Telekom · Bonn', 7.1, 50.74, '276'],
    ['Sky · Isleworth', -.34, 51.47, '826'],
    ['Xiaomi · Beijing', 116.4, 39.9, '156'], ['TCL · Huizhou', 114.42, 23.11, '156'],
    ['Skyworth · Shenzhen', 114.06, 22.54, '156'], ['Haier · Qingdao', 120.38, 36.07, '156'],
    ['ZTE · Shenzhen', 114.06, 22.54, '156'], ['Alibaba · Hangzhou', 120.16, 30.27, '156'],
    ['Baidu · Beijing', 116.4, 39.9, '156'], ['China Mobile · Beijing', 116.4, 39.9, '156'],
    ['China Telecom · Beijing', 116.4, 39.9, '156'], ['China Unicom · Beijing', 116.4, 39.9, '156'],
    ['JBL · Stamford', -73.54, 41.05, '840'], ['Harman Kardon · Stamford', -73.54, 41.05, '840'],
    ['Reliance Jio · Navi Mumbai', 73.03, 19.03, '356'],
  ].map(([name, longitude, latitude, region]) => ({ name, coordinates: [longitude, latitude], region, kind: 'customer' })))

export function selectGlobeConnections(random = Math.random) {
  return ['Beijing', 'Shanghai', 'Shenzhen'].map(name => {
    const from = globalLocations.find(location => location.kind === 'office' && location.name === name)
    // A city cannot have a visible arc to its own city-center coordinate.
    const customers = globalLocations.filter(location => location.kind === 'customer' && geoDistance(from.coordinates, location.coordinates) > .001)
    const to = customers[Math.min(customers.length - 1, Math.floor(random() * customers.length))]
    const interpolate = geoInterpolate(from.coordinates, to.coordinates)
    return { from, to, interpolate, geometry: { type: 'LineString', coordinates: Array.from({ length: 49 }, (_, i) => interpolate(i / 48)) } }
  })
}

export function createGlobalGlobe(context, { framing = 'partial', random = Math.random } = {}) {
  const projection = geoOrthographic().clipAngle(90).precision(.7)
  const path = geoPath(projection, context)
  const graticule = geoGraticule10()
  const labelWidths = new Map()
  let started
  let round = -1
  let connections = []
  return (width, height, now, dark) => {
    started ??= now
    const longitude = 110 + (now - started) / 500
    const loginView = framing === 'login'
    const latitude = loginView ? 60 * Math.sin((now - started) / 240000 * Math.PI * 2 + Math.asin(.3)) : 18
    const radius = loginView ? Math.min(width, height) * .72 : Math.min(width * .42, height * .86)
    const center = loginView ? [width * .27, height * .76] : [width * .78, height * .73]
    const facing = [longitude, latitude]
    const visible = coordinates => geoDistance(coordinates, facing) < Math.PI / 2
    projection.rotate([-longitude, -latitude, loginView ? -23.5 : 0]).scale(radius).translate(center).clipExtent([[0, 0], [width, height]])
    context.save()
    context.beginPath()
    context.arc(...center, radius, 0, Math.PI * 2)
    context.clip()
    const ocean = context.createRadialGradient(center[0] - radius * .35, center[1] - radius * .35, radius * .08, ...center, radius)
    ocean.addColorStop(0, dark ? '#234961' : '#d3e8ee')
    ocean.addColorStop(.75, dark ? '#102b40' : '#bbd5df')
    ocean.addColorStop(1, dark ? '#071725' : '#8eadbe')
    context.fillStyle = ocean
    context.fill()
    function paint(geometry, fill, stroke, alpha, lineWidth = .7) {
      context.globalAlpha = alpha
      context.beginPath()
      path(geometry)
      if (fill) { context.fillStyle = fill; context.fill() }
      if (stroke) { context.strokeStyle = stroke; context.lineWidth = lineWidth; context.stroke() }
    }
    paint(boundaries.land, dark ? '#4c8b94' : '#91b9bc', dark ? '#9cd0d2' : '#477f8b', .75)
    paint(detail.admin1, null, dark ? '#87b7bc' : '#537f89', .42, .45)
    paint(boundaries.borders, null, dark ? '#cae6e8' : '#335e71', .7, .8)
    paint(graticule, null, dark ? '#62a3b3' : '#487f95', .24, .5)
    const boxes = []
    function label(text, x, y, color, font = 10) {
      if (width < 600 || height < 160) return
      context.font = `${font}px system-ui, sans-serif`
      const key = `${font}:${text}`
      if (!labelWidths.has(key)) labelWidths.set(key, context.measureText(text).width)
      const size = labelWidths.get(key)
      const box = [x, y - font, x + size + 3, y + 3]
      if ([box[0], box[2]].some(px => [box[1], box[3]].some(py => Math.hypot(px - center[0], py - center[1]) > radius - 2))) return
      if (box[0] < 4 || box[2] > width - 4 || box[1] < 4 || box[3] > height - 4 || boxes.some(b => box[0] < b[2] && box[2] > b[0] && box[1] < b[3] && box[3] > b[1])) return
      boxes.push(box)
      context.fillStyle = color
      context.globalAlpha = .9
      context.fillText(text, x, y)
    }
    if (width >= 600 && height >= 160) {
      const named = new Set()
      context.font = '10px system-ui, sans-serif'
      context.fillStyle = dark ? '#b7d6dd' : '#365c70'
      context.globalAlpha = .7
      for (const [name, lon, lat] of geographyLabels) {
        if (named.has(name) || !visible([lon, lat])) continue
        const [x, y] = projection([lon, lat])
        const size = context.measureText(name).width
        if (x - size / 2 < 0 || x + size / 2 > width || y < 12 || y > height - 8) continue
        label(name, x - size / 2, y, dark ? '#a8cbd6' : '#365c70')
        named.add(name)
      }
    }
    const currentRound = Math.floor((now - started) / 10000)
    if (currentRound !== round) { round = currentRound; connections = selectGlobeConnections(random) }
    for (const [index, route] of connections.entries()) {
      paint(route.geometry, null, dark ? '#48d6f1' : '#078dab', .5, 1.2)
      const progress = Math.max(0, Math.min(1, ((now - started) % 10000 - index * 700) / 7000))
      const tail = { type: 'LineString', coordinates: Array.from({ length: 9 }, (_, i) => route.interpolate(Math.max(0, progress - .12) + Math.min(.12, progress) * i / 8)) }
      paint(tail, null, dark ? '#e0fbff' : '#0088c0', .95, 2.5)
      const head = route.interpolate(progress)
      if (visible(head)) {
        context.beginPath(); context.arc(...projection(head), 2, 0, Math.PI * 2)
        context.fillStyle = dark ? '#ffffff' : '#0077a6'; context.fill()
      }
    }
    const visibleLocations = globalLocations.filter(location => visible(location.coordinates))
    for (const kind of ['office', 'customer']) {
      const candidates = visibleLocations.filter(location => location.kind === kind)
      const color = kind === 'office' ? (dark ? '#64d9e8' : '#007f9a') : (dark ? '#f0b880' : '#a45d35')
      const labelColor = kind === 'office' ? (dark ? '#a6f4ff' : '#0086ad') : (dark ? '#ffdda6' : '#bd6923')
      for (const location of candidates) {
        const [x, y] = projection(location.coordinates)
        context.globalAlpha = .95
        context.beginPath()
        context.arc(x, y, kind === 'office' ? 3.2 : 2.6, 0, Math.PI * 2)
        context.fillStyle = color
        context.fill()
        label(`${kind === 'office' ? 'Office' : 'Customer'} · ${location.name}`, x + 7, y + (kind === 'office' ? -7 : 14), labelColor, 11)
      }
    }
    for (const [name, lon, lat, level] of detail.centers) {
      if (!visible([lon, lat])) continue
      const [x, y] = projection([lon, lat])
      context.globalAlpha = level ? .6 : .9
      context.fillStyle = dark ? '#d2e4df' : '#335e6b'
      context.beginPath(); context.arc(x, y, level ? .85 : 1.5, 0, Math.PI * 2); context.fill()
      label(name, x + 4, y - 3, dark ? '#b9d4d6' : '#3e6370', level ? 9 : 10)
    }
    context.restore()
    context.globalAlpha = 1
  }
}
