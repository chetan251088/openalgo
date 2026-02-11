import type { AutoscaleInfoProvider } from 'lightweight-charts'

function sanitizeStep(step: number): number {
  if (!Number.isFinite(step) || step <= 0) return 1
  return step
}

function sanitizeMinSpan(minSpan: number, step: number): number {
  if (!Number.isFinite(minSpan) || minSpan <= 0) return step * 2
  const rounded = Math.ceil(minSpan / step) * step
  return Math.max(step * 2, rounded)
}

/**
 * Creates a stepped autoscale provider for lightweight-charts.
 *
 * Example:
 * - step=10, minSpan=40 -> axis tends to show 80/90/100/110/120 around 100
 * - step=50, minSpan=200 -> index axis shows 50-point grid context
 */
export function createSteppedAutoscaleProvider(step: number, minSpan: number): AutoscaleInfoProvider {
  const safeStep = sanitizeStep(step)
  const safeMinSpan = sanitizeMinSpan(minSpan, safeStep)

  return (baseImplementation) => {
    const base = baseImplementation()
    const range = base?.priceRange
    if (!base || !range) return base

    const rawMin = range.minValue
    const rawMax = range.maxValue
    if (!Number.isFinite(rawMin) || !Number.isFinite(rawMax)) return base

    const min = Math.min(rawMin, rawMax)
    const max = Math.max(rawMin, rawMax)
    const center = (min + max) / 2

    let span = max - min
    if (!Number.isFinite(span) || span < safeStep) span = safeStep
    if (span < safeMinSpan) span = safeMinSpan
    span = Math.ceil(span / safeStep) * safeStep

    const half = span / 2
    const snappedMin = Math.floor((center - half) / safeStep) * safeStep
    let snappedMax = Math.ceil((center + half) / safeStep) * safeStep
    if (snappedMax - snappedMin < span) {
      snappedMax = snappedMin + span
    }

    return {
      ...base,
      priceRange: {
        minValue: snappedMin,
        maxValue: snappedMax,
      },
    }
  }
}

