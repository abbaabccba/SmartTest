export const PROJECT_STATUSES = Object.freeze(['NORMAL', 'WARNING', 'BLOCK'])

export const PROJECT_STAGES = Object.freeze([
  '1 EVALUATION', '2 IN DEVELOPMENT', '3 SYSTEM UPGRADE', '4 MP MAINTENANCE', '5 MP CLOSE',
  '6 POC CLOSE', '7 PENDING', '8 CANCEL KICKOFF', '9 CANCEL CLOSE', 'Unspecified',
])

const stageByName = new Map(PROJECT_STAGES.map(value => [value.toUpperCase(), value]))

export function projectStatus(value) {
  return String(value ?? '').trim().match(/^(NORMAL|WARNING|BLOCK)\b/i)?.[1].toUpperCase()
}

export function bindProjectStatus(element, value) {
  const status = projectStatus(value)
  if (status) element.dataset.projectStatus = status
  return element
}

export function bindProjectStatusText(element, value) {
  const status = projectStatus(value)
  if (status) element.dataset.projectStatusText = status
  return element
}

export function bindProjectStage(element, value) {
  const stage = stageByName.get(String(value ?? '').trim().toUpperCase())
  if (stage) element.dataset.projectStage = stage
  return element
}
