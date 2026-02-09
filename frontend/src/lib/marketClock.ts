import type { MarketClockZone } from '@/types/scalping'

/**
 * Market clock utilities for time-of-day awareness.
 * All times are in IST (UTC+5:30).
 */

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

/**
 * Get the current IST time as { hours, minutes } from a UTC Date.
 */
export function getISTTime(now: Date = new Date()) {
  const utc = now.getTime() + now.getTimezoneOffset() * 60000
  const ist = new Date(utc + 5.5 * 60 * 60 * 1000)
  return { hours: ist.getUTCHours(), minutes: ist.getUTCMinutes() }
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
    const expiryDate = new Date(expiry)
    // Use IST date for comparison
    const utc = today.getTime() + today.getTimezoneOffset() * 60000
    const ist = new Date(utc + 5.5 * 60 * 60 * 1000)

    return (
      expiryDate.getFullYear() === ist.getUTCFullYear() &&
      expiryDate.getMonth() === ist.getUTCMonth() &&
      expiryDate.getDate() === ist.getUTCDate()
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
  const utc = now.getTime() + now.getTimezoneOffset() * 60000
  const ist = new Date(utc + 5.5 * 60 * 60 * 1000)
  return `${ist.getUTCHours().toString().padStart(2, '0')}:${ist
    .getUTCMinutes()
    .toString()
    .padStart(2, '0')}:${ist.getUTCSeconds().toString().padStart(2, '0')}`
}
