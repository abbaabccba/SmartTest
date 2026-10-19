import { downloadExcel } from './excel-download.js'

const DIRECTIONS = ['uplink', 'downlink']

function directionOf(row, dataType) {
  const value = `${row?.direction ?? ''}`.trim().toLowerCase()
  if (value === 'tx' || value === 'uplink') return 'uplink'
  if (value === 'rx' || value === 'downlink') return 'downlink'
  return dataType === 'RVR' || dataType === 'RVO' ? 'uplink' : null
}

function scenarioValue(key, name) {
  const match = `${key ?? ''}`.match(new RegExp(`(?:^|\\|)\\s*${name}\\s*=\\s*([^|]+)`, 'i'))
  return match?.[1]?.trim() ?? null
}

function formatBand(value) {
  const raw = `${value ?? ''}`.trim()
  if (!raw) return ''
  const normalized = raw.replace(/band\s*/i, '').replace(/\s+/g, '').replace(/ghz$/i, '').replace(/g$/i, '')
  const numeric = Number.parseFloat(normalized)
  return Number.isFinite(numeric) ? `${Number(numeric.toFixed(2))}G` : `${normalized}G`
}

function descriptorOf(row) {
  const bandwidth = Number.parseFloat(scenarioValue(row.scenarioGroupKey, 'BANDWIDTH') ?? scenarioValue(row.scenarioGroupKey, 'BW') ?? '')
  const parsedChannel = Number.parseInt(scenarioValue(row.scenarioGroupKey, 'CHANNEL') ?? '', 10)
  return {
    band: row.band ?? scenarioValue(row.scenarioGroupKey, 'BAND'),
    standard: row.standard ?? scenarioValue(row.scenarioGroupKey, 'STANDARD'),
    bandwidthMhz: Number.isFinite(row.bandwidthMhz) ? row.bandwidthMhz : (Number.isFinite(bandwidth) ? bandwidth : null),
    channel: channelOf(row) ?? (Number.isFinite(parsedChannel) ? parsedChannel : null)
  }
}

function scenarioOf(row) {
  const descriptor = descriptorOf(row)
  const keyParts = []
  if (descriptor.band) keyParts.push(`BAND=${`${descriptor.band}`.toUpperCase()}`)
  if (descriptor.standard) keyParts.push(`STANDARD=${`${descriptor.standard}`.toUpperCase()}`)
  if (Number.isFinite(descriptor.bandwidthMhz)) keyParts.push(`BW=${descriptor.bandwidthMhz}`)
  if (Number.isFinite(descriptor.channel)) keyParts.push(`CHANNEL=${descriptor.channel}`)
  const label = [formatBand(descriptor.band), descriptor.standard && `${descriptor.standard}`.toUpperCase(),
    Number.isFinite(descriptor.bandwidthMhz) && `${descriptor.bandwidthMhz}MHz`,
    Number.isFinite(descriptor.channel) && `CH ${descriptor.channel}`].filter(Boolean).join(' · ') || row.casePath || 'Scenario'
  return { key: keyParts.join('|') || row.casePath || row.scenarioGroupKey || 'scenario', label }
}

function channelOf(row) {
  const value = Number(row.centerFreqMhz)
  if (!Number.isFinite(value)) return row.channel ?? null
  if (value >= 2412 && value <= 2472) return Math.round((value - 2407) / 5)
  if (value === 2484) return 14
  if (value >= 5000 && value < 5950) return Math.round((value - 5000) / 5)
  if (value >= 5925 && value <= 7125) return Math.round((value - 5950) / 5)
  return value
}

function seriesOf(row) {
  const key = `${row.testReportId ?? 'unknown'}__${row.scenarioGroupKey ?? ''}`
  const project = `${row.projectNickname || (row.projectId != null ? `Project ${row.projectId}` : row.project || 'Unknown Project')}`
  const channel = descriptorOf(row).channel
  return { key, label: Number.isFinite(channel) ? `${project} CH ${channel}` : project }
}

function scenarioMap() {
  return new Map()
}

function ensureScenario(scenarios, row) {
  const identity = scenarioOf(row)
  if (!scenarios.has(identity.key)) {
    scenarios.set(identity.key, { ...identity, directions: { uplink: new Map(), downlink: new Map() } })
  }
  return scenarios.get(identity.key)
}

function finalize(scenarios, convert) {
  return [...scenarios.values()].map(scenario => ({
    key: scenario.key,
    label: scenario.label,
    directions: Object.fromEntries(DIRECTIONS.map(direction => [
      direction,
      [...scenario.directions[direction].values()].map(convert)
    ]))
  }))
}

export function groupLineSeries(rows, dataType) {
  const scenarios = scenarioMap()
  for (const row of rows ?? []) {
    const direction = directionOf(row, dataType)
    if (!direction || !Number.isFinite(row.pathLossDb) || !Number.isFinite(row.throughputAvgMbps)) continue
    const bucket = ensureScenario(scenarios, row).directions[direction]
    const series = seriesOf(row)
    if (!bucket.has(series.key)) bucket.set(series.key, { label: series.label, points: [] })
    bucket.get(series.key).points.push({ x: row.pathLossDb, y: row.throughputAvgMbps })
  }
  return finalize(scenarios, group => ({ ...group, points: group.points.sort((a, b) => a.x - b.x) }))
}

export function groupPolarSeries(rows, dataType) {
  const scenarios = scenarioMap()
  for (const row of rows ?? []) {
    const direction = directionOf(row, dataType)
    if (!direction || !Number.isFinite(row.angleDeg) || !Number.isFinite(row.throughputAvgMbps)) continue
    const bucket = ensureScenario(scenarios, row).directions[direction]
    const series = seriesOf(row)
    if (!bucket.has(series.key)) bucket.set(series.key, { label: series.label, angles: new Map() })
    const values = bucket.get(series.key).angles.get(row.angleDeg) ?? []
    values.push(row.throughputAvgMbps)
    bucket.get(series.key).angles.set(row.angleDeg, values)
  }
  return [...scenarios.values()].map(scenario => ({
    key: scenario.key, label: scenario.label,
    directions: Object.fromEntries(DIRECTIONS.map(direction => {
      const groups = [...scenario.directions[direction].values()]
      const observed = [...new Set(groups.flatMap(group => [...group.angles.keys()]))].sort((a, b) => a - b)
      const step = observed.some(angle => Math.abs(angle % 30) < 1e-9) ? 30
        : observed.some(angle => Math.abs(angle % 45) < 1e-9) ? 45
          : observed.some(angle => Math.abs(angle % 15) < 1e-9) ? 15 : 45
      const angles = observed.length > 0 && observed.length < 6
        ? Array.from({ length: Math.floor(360 / step) + 1 }, (_, index) => index * step) : observed
      return [direction, groups.map(group => ({ label: group.label, points: angles.map(angle => {
        const values = group.angles.get(angle) ?? []
        return { angle, throughput: values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : null }
      }) }))]
    }))
  }))
}

const colors = ['#321fdb', '#3399ff', '#2eb85c', '#f9b115', '#e55353', '#6f42c1']

export function createChartController({ chartFactory }) {
  let instances = []
  function clear() {
    for (const instance of instances) instance.destroy()
    instances = []
  }
  function render(container, rows, dataType) {
    clear()
    container.replaceChildren()
    const scenarios = dataType === 'RVO' ? groupPolarSeries(rows, dataType) : groupLineSeries(rows, dataType)
    for (const scenario of scenarios) {
      const section = document.createElement('section')
      section.className = 'scenario'
      const title = document.createElement('h2')
      title.textContent = scenario.label
      section.append(title)
      for (const direction of DIRECTIONS) {
        const groups = scenario.directions[direction]
        if (!groups.length) continue
        const heading = document.createElement('h3')
        heading.textContent = direction === 'uplink' ? 'Tx (Uplink)' : 'Rx (Downlink)'
        const canvas = document.createElement('canvas')
        canvas.dataset.exportTitle = `${scenario.label} - ${heading.textContent}`
        section.append(heading, canvas)
        const config = dataType === 'RVO' ? polarConfig(groups) : lineConfig(groups)
        instances.push(chartFactory(canvas, config))
      }
      container.append(section)
    }
    if (!instances.length) container.textContent = 'No matching performance data.'
  }
  return { clear, render }
}

function lineConfig(groups) {
  return {
    type: 'line',
    data: { datasets: groups.map((group, index) => ({
      label: group.label,
      data: group.points,
      borderColor: colors[index % colors.length],
      backgroundColor: colors[index % colors.length],
      tension: 0.2
    })) },
    options: { parsing: false, responsive: true, plugins: { legend: { display: true } }, scales: {
      x: { type: 'linear', title: { display: true, text: 'Path Loss (dB)' } },
      y: { beginAtZero: true, title: { display: true, text: 'Throughput (Mbps)' } }
    } }
  }
}

function polarConfig(groups) {
  const angles = [...new Set(groups.flatMap(group => group.points.map(point => point.angle)))].sort((a, b) => a - b)
  return {
    type: 'radar',
    data: {
      labels: angles.map(angle => `${angle}°`),
      datasets: groups.map((group, index) => ({
        label: group.label,
        data: angles.map(angle => group.points.find(point => point.angle === angle)?.throughput ?? null),
        borderColor: colors[index % colors.length],
        backgroundColor: `${colors[index % colors.length]}22`,
        spanGaps: true
      }))
    },
    options: { responsive: true, scales: { r: { beginAtZero: true, title: { display: true, text: 'Throughput (Mbps)' } } } }
  }
}


export async function exportPerformanceExcel(rows, { Workbook, saveFile = downloadExcel, now = new Date() }) {
  if (!rows?.length) throw new Error('No data available to export.')
  const workbook = new Workbook()
  const worksheet = workbook.addWorksheet('Performance')
  const columns = ['Index', 'Path_Loss_dB', 'Throughput_Avg_Mbps', 'Direction', 'Band', 'Bandwidth_MHz',
    'Channel', 'Center_Freq_MHz', 'Standard', 'Test_Category', 'Protocol', 'Case_Path', 'Product_Line',
    'Project', 'ADB_Device', 'Telnet_IP', 'Created_At']
  worksheet.columns = columns.map(key => ({ header: key, key }))
  worksheet.addRows(rows.map((row, index) => ({
    Index: index + 1, Path_Loss_dB: row.pathLossDb, Throughput_Avg_Mbps: row.throughputAvgMbps,
    Direction: directionOf(row, row.dataType) === 'downlink' ? 'Rx' : 'Tx', Band: formatBand(row.band) || row.band,
    Bandwidth_MHz: row.bandwidthMhz, Channel: channelOf(row), Center_Freq_MHz: row.centerFreqMhz,
    Standard: row.standard, Test_Category: row.testCategory, Protocol: row.protocol, Case_Path: row.casePath,
    Product_Line: row.productLine, Project: row.project, ADB_Device: row.adbDevice, Telnet_IP: row.telnetIp,
    Created_At: row.createdAt
  })))
  const buffer = await workbook.xlsx.writeBuffer()
  const timestamp = now.toISOString().replace(/[:T]/g, '-').split('.')[0]
  saveFile(buffer, `wifi-performance-${timestamp}.xlsx`)
}

export function exportVisibleChartsPdf(container, {
  JsPdf, createCanvas = () => document.createElement('canvas'),
  background = getComputedStyle(document.documentElement).getPropertyValue('--bg-primary').trim() || '#ffffff',
  dataType = 'Wi-Fi performance'
}) {
  const canvases = [...container.querySelectorAll('canvas')].filter(canvas => canvas.width > 0 && canvas.height > 0)
  if (!canvases.length) throw new Error('No charts available for PDF export.')
  const documentPdf = new JsPdf({ orientation: 'landscape', unit: 'pt', format: 'a4', compress: true })
  const pageWidth = documentPdf.internal.pageSize.getWidth()
  const pageHeight = documentPdf.internal.pageSize.getHeight()
  const margin = 24; const gap = 14; const titleHeight = 36
  const availableWidth = pageWidth - margin * 2
  const slotHeight = (pageHeight - margin * 2 - gap) / 2
  canvases.forEach((canvas, index) => {
    if (index && index % 2 === 0) documentPdf.addPage()
    const top = margin + (index % 2) * (slotHeight + gap)
    documentPdf.setFont('helvetica', 'bold'); documentPdf.setFontSize(13)
    documentPdf.text(canvas.dataset.exportTitle || 'Chart', margin, top + 16)
    documentPdf.setFont('helvetica', 'normal'); documentPdf.setFontSize(10)
    documentPdf.text(`Type: ${dataType}`, margin, top + 32)
    const composed = createCanvas(); const padding = 18
    composed.width = canvas.width + padding * 2; composed.height = canvas.height + padding * 2
    const context = composed.getContext('2d')
    if (!context) throw new Error('Failed to create PDF export canvas.')
    context.fillStyle = background; context.fillRect(0, 0, composed.width, composed.height)
    context.drawImage(canvas, padding, padding)
    const scale = Math.min(availableWidth / composed.width, (slotHeight - titleHeight) / composed.height)
    const width = composed.width * scale; const height = composed.height * scale
    documentPdf.addImage(composed.toDataURL('image/png'), 'PNG', margin + (availableWidth - width) / 2, top + titleHeight, width, height)
  })
  documentPdf.save(`wifi-performance-${new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-')}.pdf`)
}
