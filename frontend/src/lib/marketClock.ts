import type { MarketClockZone } from '@/types/scalping'

/**
 * Market clock utilities for time-of-day awareness.
 * All times are in IST (UTC+5:30).
 */

const IST_TIME_ZONE = 'Asia/Kolkata'
const IST_PARTS_FORMATTER = new Intl.DateTimeFormat('en-GB', {
  timeZone: IST_TIME_ZONE,
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  hour12: false,
  hourCycle: 'h23',
})

const MONTH_MAP: Record<string, number> = {
  JAN: 1,
  FEB: 2,
  MAR: 3,
  APR: 4,
  MAY: 5,
  JUN: 6,
  JUL: 7,
  AUG: 8,
  SEP: 9,
  OCT: 10,
  NOV: 11,
  DEC: 12,
}

// Standard hot zones
const STANDARD_ZONES: MarketClockZone[] = [
  { label: 'Pre-Open', start: '09:00', end: '09:15', sensitivity: 0 },
  { label: 'Opening Momentum', start: '09:15', end: '09:30', sensitivity: 1.5 },
  { label: 'Morning Session', start: '09:30', end: '11:30', sensitivity: 1.0 },
  { label: 'Quiet Zone', start: '11:30', end: '12:30', sensitivity: 0.5 },
  { label: 'Afternoon Action', start: '12:30', end: '13:00', sensitivity: 1.2 },
  { label: 'Afternoon', start: '13:00', end: '14:00', sensitivity: 0.8 },
  { label: 'Closing Rally', start: '14:00', end: '15:00', sensitivity: 1.3 },
  { label: 'Final Push', start: '15:00', end: '15:30', sensitivity: 1.5 },
]

// Expiry day has different zones
const EXPIRY_ZONES: MarketClockZone[] = [
  { label: 'Pre-Open', start: '09:00', end: '09:15', sensitivity: 0 },
  { label: 'Expiry Open', start: '09:15', end: '09:30', sensitivity: 1.8 },
  { label: 'Morning Expiry', start: '09:30', end: '11:30', sensitivity: 1.2 },
  { label: 'Expiry Quiet', start: '11:30', end: '13:00', sensitivity: 0.6 },
  { label: 'Surprise Zone', start: '13:00', end: '14:00', sensitivity: 1.5 },
  { label: 'Panic Zone', start: '14:00', end: '15:00', sensitivity: 2.0 },
  { label: 'Expiry Close', start: '15:00', end: '15:30', sensitivity: 2.0 },
]

function timeToMinutes(timeStr: string): number {
  const [h, m] = timeStr.split(':').map(Number)
  return h * 60 + m
}

function getISTDateParts(now: Date = new Date()) {
  const partMap: Record<string, string> = {}
  for (const part of IST_PARTS_FORMATTER.formatToParts(now)) {
    if (part.type !== 'literal') {
      partMap[part.type] = part.value
    }
  }

  return {
    year: Number(partMap.year),
    month: Number(partMap.month),
    day: Number(partMap.day),
    hours: Number(partMap.hour),
    minutes: Number(partMap.minute),
    seconds: Number(partMap.second),
  }
}

function parseExpiryDateParts(expiry: string, referenceDate: Date = new Date()) {
  const normalized = expiry.trim().toUpperCase()
  if (!normalized) return null

  const isoMatch = normalized.match(/^(\d{4})-(\d{2})-(\d{2})$/)
  if (isoMatch) {
    return {
      year: Number(isoMatch[1]),
      month: Number(isoMatch[2]),
      day: Number(isoMatch[3]),
    }
  }

  const dmyMatch = normalized.match(/^(\d{1,2})-([A-Z]{3})-(\d{2}|\d{4})$/)
  if (dmyMatch) {
    const month = MONTH_MAP[dmyMatch[2]]
    if (!month) return null
    const day = Number(dmyMatch[1])
    const yearRaw = dmyMatch[3]
    const year = yearRaw.length === 2 ? 2000 + Number(yearRaw) : Number(yearRaw)
    return { year, month, day }
  }

  const dmMatch = normalized.match(/^(\d{1,2})-([A-Z]{3})$/)
  if (dmMatch) {
    const month = MONTH_MAP[dmMatch[2]]
    if (!month) return null
    const { year } = getISTDateParts(referenceDate)
    return { year, month, day: Number(dmMatch[1]) }
  }

  return null
}

/**
 * Get the current IST time as { hours, minutes } from a UTC Date.
 */
export function getISTTime(now: Date = new Date()) {
  const { hours, minutes } = getISTDateParts(now)
  return { hours, minutes }
}

/**
 * Get hot zones for the given day type.
 */
export function getHotZones(isExpiryDay: boolean): MarketClockZone[] {
  return isExpiryDay ? EXPIRY_ZONES : STANDARD_ZONES
}

/**
 * Get current zone based on IST time.
 */
export function getCurrentZone(
  now: Date = new Date(),
  isExpiryDay = false
): { zone: MarketClockZone | null; sensitivity: number } {
  const { hours, minutes } = getISTTime(now)
  const currentMins = hours * 60 + minutes
  const zones = getHotZones(isExpiryDay)

  for (const zone of zones) {
    const start = timeToMinutes(zone.start)
    const end = timeToMinutes(zone.end)
    if (currentMins >= start && currentMins < end) {
      return { zone, sensitivity: zone.sensitivity }
    }
  }

  return { zone: null, sensitivity: 0 }
}

/**
 * Get minutes until next zone starts.
 */
export function getMinutesToNextZone(
  now: Date = new Date(),
  isExpiryDay = false
): { nextZone: MarketClockZone | null; minutesUntil: number } {
  const { hours, minutes } = getISTTime(now)
  const currentMins = hours * 60 + minutes
  const zones = getHotZones(isExpiryDay)

  for (const zone of zones) {
    const start = timeToMinutes(zone.start)
    if (start > currentMins) {
      return { nextZone: zone, minutesUntil: start - currentMins }
    }
  }

  return { nextZone: null, minutesUntil: 0 }
}

/**
 * Check if a given expiry date string is today.
 * Expiry format: "YYYY-MM-DD" or "DD-MMM-YYYY" etc.
 */
export function isExpiryDate(expiry: string, today: Date = new Date()): boolean {
  if (!expiry) return false

  try {
    const expiryDateParts = parseExpiryDateParts(expiry, today)
    if (!expiryDateParts) return false

    const istToday = getISTDateParts(today)
    return (
      expiryDateParts.year === istToday.year &&
      expiryDateParts.month === istToday.month &&
      expiryDateParts.day === istToday.day
    )
  } catch {
    return false
  }
}

/**
 * Check if market is currently open (9:15 - 15:30 IST).
 */
export function isMarketOpen(now: Date = new Date()): boolean {
  const { hours, minutes } = getISTTime(now)
  const currentMins = hours * 60 + minutes
  return currentMins >= 9 * 60 + 15 && currentMins < 15 * 60 + 30
}

/**
 * Format IST time as HH:MM:SS string.
 */
export function formatISTTime(now: Date = new Date()): string {
  const { hours, minutes, seconds } = getISTDateParts(now)
  return `${hours.toString().padStart(2, '0')}:${minutes.toString().padStart(2, '0')}:${seconds
    .toString()
    .padStart(2, '0')}`
}
