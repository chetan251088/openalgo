const IST_OFFSET_SECONDS = 5 * 60 * 60 + 30 * 60
const SECONDS_PER_DAY = 24 * 60 * 60
const MARKET_OPEN_SECONDS = 9 * 60 * 60 + 15 * 60
const MARKET_CLOSE_SECONDS = 15 * 60 * 60 + 30 * 60

function normalizeEpochSeconds(raw: number): number {
  if (!Number.isFinite(raw)) return 0
  if (raw > 1e12) return Math.floor(raw / 1000)
  if (raw > 1e10) return Math.floor(raw / 1000)
  return Math.floor(raw)
}

function getIstDayParts(epochSeconds: number): {
  dayStartIstSeconds: number
  secondsOfDay: number
  dayOfWeek: number
} {
  const istEpochSeconds = normalizeEpochSeconds(epochSeconds) + IST_OFFSET_SECONDS
  const dayStartIstSeconds = Math.floor(istEpochSeconds / SECONDS_PER_DAY) * SECONDS_PER_DAY
  const secondsOfDay = istEpochSeconds - dayStartIstSeconds
  const dayStartUtcMillis = (dayStartIstSeconds - IST_OFFSET_SECONDS) * 1000
  const dayOfWeek = new Date(dayStartUtcMillis).getUTCDay()
  return { dayStartIstSeconds, secondsOfDay, dayOfWeek }
}

function parseNaiveIstDateTime(value: string): number | null {
  const match = value.match(
    /^(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{2}):(\d{2})(?::(\d{2})(?:\.\d{1,3})?)?)?$/
  )
  if (!match) return null

  const year = Number(match[1])
  const month = Number(match[2])
  const day = Number(match[3])
  const hour = Number(match[4] ?? '0')
  const minute = Number(match[5] ?? '0')
  const second = Number(match[6] ?? '0')

  if (
    !Number.isFinite(year) ||
    !Number.isFinite(month) ||
    !Number.isFinite(day) ||
    !Number.isFinite(hour) ||
    !Number.isFinite(minute) ||
    !Number.isFinite(second)
  ) {
    return null
  }

  const utcMillis = Date.UTC(year, month - 1, day, hour, minute, second) - IST_OFFSET_SECONDS * 1000
  return Math.floor(utcMillis / 1000)
}

function parseBusinessDayObject(value: unknown): number | null {
  if (!value || typeof value !== 'object') return null

  const raw = value as { year?: unknown; month?: unknown; day?: unknown }
  const year = Number(raw.year)
  const month = Number(raw.month)
  const day = Number(raw.day)

  if (
    !Number.isFinite(year) ||
    !Number.isFinite(month) ||
    !Number.isFinite(day) ||
    year < 1970 ||
    month < 1 ||
    month > 12 ||
    day < 1 ||
    day > 31
  ) {
    return null
  }

  const utcMillis =
    Date.UTC(Math.floor(year), Math.floor(month) - 1, Math.floor(day), 0, 0, 0) -
    IST_OFFSET_SECONDS * 1000
  return Math.floor(utcMillis / 1000)
}

export function parseHistoryTimestampToEpochSeconds(value: unknown): number | null {
  if (typeof value === 'number') {
    return normalizeEpochSeconds(value)
  }

  const businessDaySeconds = parseBusinessDayObject(value)
  if (businessDaySeconds != null) return businessDaySeconds

  if (typeof value !== 'string') return null
  const trimmed = value.trim()
  if (trimmed.length === 0) return null

  const numeric = Number(trimmed.replace(/,/g, ''))
  if (Number.isFinite(numeric)) {
    return normalizeEpochSeconds(numeric)
  }

  const naiveIstSeconds = parseNaiveIstDateTime(trimmed)
  if (naiveIstSeconds != null) return naiveIstSeconds

  const parsedMillis = Date.parse(trimmed)
  if (Number.isFinite(parsedMillis)) {
    return Math.floor(parsedMillis / 1000)
  }

  return null
}

export function isWithinIndiaMarketHours(
  epochSeconds: number,
  options: { includeClose?: boolean } = {}
): boolean {
  const { secondsOfDay, dayOfWeek } = getIstDayParts(epochSeconds)
  if (dayOfWeek === 0 || dayOfWeek === 6) return false

  const afterOpen = secondsOfDay >= MARKET_OPEN_SECONDS
  const beforeClose = options.includeClose
    ? secondsOfDay <= MARKET_CLOSE_SECONDS
    : secondsOfDay < MARKET_CLOSE_SECONDS
  return afterOpen && beforeClose
}

export function alignToIndiaMarketInterval(epochSeconds: number, intervalSec: number): number {
  const safeInterval = Math.max(1, Math.floor(intervalSec))
  const { dayStartIstSeconds, secondsOfDay } = getIstDayParts(epochSeconds)
  const anchoredSeconds =
    secondsOfDay <= MARKET_OPEN_SECONDS
      ? MARKET_OPEN_SECONDS
      : MARKET_OPEN_SECONDS +
        Math.floor((secondsOfDay - MARKET_OPEN_SECONDS) / safeInterval) * safeInterval
  return dayStartIstSeconds + anchoredSeconds - IST_OFFSET_SECONDS
}

export function formatIstHmFromEpoch(epochSeconds: number): string {
  const istMillis = (normalizeEpochSeconds(epochSeconds) + IST_OFFSET_SECONDS) * 1000
  const d = new Date(istMillis)
  const hh = d.getUTCHours().toString().padStart(2, '0')
  const mm = d.getUTCMinutes().toString().padStart(2, '0')
  return `${hh}:${mm}`
}
