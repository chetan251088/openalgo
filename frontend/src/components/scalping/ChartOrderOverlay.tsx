import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  LineStyle,
  type IChartApi,
  type ISeriesApi,
  type IPriceLine,
  type MouseEventParams,
} from 'lightweight-charts'
import { tradingApi } from '@/api/trading'
import { useScalpingStore } from '@/stores/scalpingStore'
import { useAuthStore } from '@/stores/authStore'
import { useVirtualOrderStore } from '@/stores/virtualOrderStore'
import { MarketDataManager } from '@/lib/MarketDataManager'
import { buildVirtualPosition, resolveEntryPrice } from '@/lib/scalpingVirtualPosition'
import { cn } from '@/lib/utils'
import type { ActiveSide, OrderAction, TriggerOrder } from '@/types/scalping'
import type { PlaceOrderRequest } from '@/types/trading'

const TICK_SIZE = 0.05
const SHOW_NATIVE_LINE_TITLES = false

function roundToTick(price: number): number {
  return Math.round(price / TICK_SIZE) * TICK_SIZE
}

function formatPts(value: number): number {
  return Number(Math.max(0, value).toFixed(2))
}

function deriveTriggerPrices(trigger: TriggerOrder) {
  const isBuy = trigger.action === 'BUY'
  return {
    tpPrice:
      trigger.tpPoints > 0
        ? roundToTick(trigger.triggerPrice + (isBuy ? trigger.tpPoints : -trigger.tpPoints))
        : null,
    slPrice:
      trigger.slPoints > 0
        ? roundToTick(trigger.triggerPrice + (isBuy ? -trigger.slPoints : trigger.slPoints))
        : null,
  }
}

interface ChartOrderOverlayProps {
  chartRef: React.RefObject<IChartApi | null>
  seriesRef: React.RefObject<ISeriesApi<'Candlestick'> | null>
  side: ActiveSide
  containerRef: React.RefObject<HTMLDivElement | null>
}

interface OverlayPos {
  y: number
  price: number
}

type DragType = 'entry' | 'tp' | 'sl'
type DragSource = 'pending' | 'trigger' | 'position'

export function ChartOrderOverlay({
  chartRef,
  seriesRef,
  side,
  containerRef,
}: ChartOrderOverlayProps) {
  const apiKey = useAuthStore((s) => s.apiKey)
  const setApiKey = useAuthStore((s) => s.setApiKey)
  const activeSide = useScalpingStore((s) => s.activeSide)
  const orderType = useScalpingStore((s) => s.orderType)
  const tpPoints = useScalpingStore((s) => s.tpPoints)
  const slPoints = useScalpingStore((s) => s.slPoints)
  const limitPrice = useScalpingStore((s) => s.limitPrice)
  const pendingEntryAction = useScalpingStore((s) => s.pendingEntryAction)
  const quantity = useScalpingStore((s) => s.quantity)
  const lotSize = useScalpingStore((s) => s.lotSize)
  const optionExchange = useScalpingStore((s) => s.optionExchange)
  const product = useScalpingStore((s) => s.product)
  const paperMode = useScalpingStore((s) => s.paperMode)
  const incrementTradeCount = useScalpingStore((s) => s.incrementTradeCount)
  const setLimitPrice = useScalpingStore((s) => s.setLimitPrice)
  const setTpPoints = useScalpingStore((s) => s.setTpPoints)
  const setSlPoints = useScalpingStore((s) => s.setSlPoints)
  const setPendingEntryAction = useScalpingStore((s) => s.setPendingEntryAction)

  const symbol = useScalpingStore((s) =>
    side === 'CE' ? s.selectedCESymbol : s.selectedPESymbol
  )

  const virtualTPSL = useVirtualOrderStore((s) => s.virtualTPSL)
  const triggerOrders = useVirtualOrderStore((s) => s.triggerOrders)
  const addTriggerOrder = useVirtualOrderStore((s) => s.addTriggerOrder)
  const updateTriggerOrder = useVirtualOrderStore((s) => s.updateTriggerOrder)
  const removeTriggerOrder = useVirtualOrderStore((s) => s.removeTriggerOrder)
  const setVirtualTPSL = useVirtualOrderStore((s) => s.setVirtualTPSL)
  const getTPSLForSymbol = useVirtualOrderStore((s) => s.getTPSLForSymbol)
  const updateVirtualTPSL = useVirtualOrderStore((s) => s.updateVirtualTPSL)
  const removeVirtualTPSL = useVirtualOrderStore((s) => s.removeVirtualTPSL)

  const trackingLineRef = useRef<IPriceLine | null>(null)
  const entryLineRef = useRef<IPriceLine | null>(null)
  const tpLineRef = useRef<IPriceLine | null>(null)
  const slLineRef = useRef<IPriceLine | null>(null)
  const trackingLineSeriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null)
  const entryLineSeriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null)
  const tpLineSeriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null)
  const slLineSeriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null)

  const [entryOverlay, setEntryOverlay] = useState<OverlayPos | null>(null)
  const [tpOverlay, setTpOverlay] = useState<OverlayPos | null>(null)
  const [slOverlay, setSlOverlay] = useState<OverlayPos | null>(null)
  const [liveLtp, setLiveLtp] = useState<number | null>(null)
  const [isClosingPosition, setIsClosingPosition] = useState(false)
  const placingLimitRef = useRef(false)

  const dragRef = useRef<{
    type: DragType
    source: DragSource
  } | null>(null)

  const isActive = activeSide === side
  const isLimitOrTrigger = orderType === 'LIMIT' || orderType === 'TRIGGER'

  const activeTPSL = useMemo(
    () => (symbol ? Object.values(virtualTPSL).find((o) => o.symbol === symbol) : undefined),
    [symbol, virtualTPSL]
  )

  const activeTrigger = useMemo(
    () => (symbol ? Object.values(triggerOrders).find((o) => o.symbol === symbol) : undefined),
    [symbol, triggerOrders]
  )

  const currentSource: DragSource | null = activeTPSL
    ? 'position'
    : activeTrigger
      ? 'trigger'
      : isActive && isLimitOrTrigger && limitPrice != null
        ? 'pending'
        : null

  const pendingAction: OrderAction = pendingEntryAction ?? 'BUY'

  // Keep a live LTP stream for active virtual positions to display real-time PnL.
  useEffect(() => {
    if (!activeTPSL) {
      setLiveLtp(null)
      return
    }

    const mdm = MarketDataManager.getInstance()
    const cachedLtp = mdm.getCachedData(activeTPSL.symbol, activeTPSL.exchange)?.data?.ltp
    if (cachedLtp && cachedLtp > 0) setLiveLtp(cachedLtp)

    const unsubscribe = mdm.subscribe(activeTPSL.symbol, activeTPSL.exchange, 'LTP', (payload) => {
      const nextLtp = payload.data.ltp
      if (nextLtp && nextLtp > 0) setLiveLtp(nextLtp)
    })

    return () => unsubscribe()
  }, [activeTPSL])

  const livePnl = useMemo(() => {
    if (!activeTPSL || !liveLtp || liveLtp <= 0) return null
    const points = activeTPSL.action === 'BUY'
      ? liveLtp - activeTPSL.entryPrice
      : activeTPSL.entryPrice - liveLtp
    const pnl = points * activeTPSL.quantity
    return {
      ltp: liveLtp,
      points,
      pnl,
    }
  }, [activeTPSL, liveLtp])

  const activeEntryTitle = useMemo(() => {
    if (!activeTPSL) return ''
    const base = `${activeTPSL.action} @ ${activeTPSL.entryPrice.toFixed(2)}`
    if (!livePnl) return base
    const pnlSign = livePnl.pnl >= 0 ? '+' : ''
    return `${base} | PnL ${pnlSign}${livePnl.pnl.toFixed(2)}`
  }, [activeTPSL, livePnl])

  const removeLine = useCallback(
    (
      lineRef: React.MutableRefObject<IPriceLine | null>,
      lineSeriesRef: React.MutableRefObject<ISeriesApi<'Candlestick'> | null>
    ) => {
      if (lineRef.current) {
        const ownerSeries = lineSeriesRef.current ?? seriesRef.current
        try {
          ownerSeries?.removePriceLine(lineRef.current)
        } catch {
          // no-op
        }
        lineRef.current = null
        lineSeriesRef.current = null
      }
    },
    [seriesRef]
  )

  const upsertLine = useCallback(
    (
      lineRef: React.MutableRefObject<IPriceLine | null>,
      lineSeriesRef: React.MutableRefObject<ISeriesApi<'Candlestick'> | null>,
      price: number,
      color: string,
      style: LineStyle,
      width: 1 | 2 | 3 | 4,
      title: string
    ) => {
      const targetSeries = seriesRef.current
      if (!targetSeries) return
      const normalizedTitle = SHOW_NATIVE_LINE_TITLES ? title : ''
      // Series can be recreated by chart lifecycle; stale handles must be detached.
      if (lineRef.current && lineSeriesRef.current && lineSeriesRef.current !== targetSeries) {
        try {
          lineSeriesRef.current.removePriceLine(lineRef.current)
        } catch {
          // no-op
        }
        lineRef.current = null
        lineSeriesRef.current = null
      }
      if (lineRef.current) {
        lineRef.current.applyOptions({
          price,
          color,
          lineStyle: style,
          lineWidth: width,
          title: normalizedTitle,
          axisLabelVisible: true,
        })
      } else {
        lineRef.current = targetSeries.createPriceLine({
          price,
          color,
          lineStyle: style,
          lineWidth: width,
          title: normalizedTitle,
          axisLabelVisible: true,
        })
        lineSeriesRef.current = targetSeries
      }
    },
    [seriesRef]
  )

  const priceToY = useCallback(
    (price: number): number | null => {
      if (!seriesRef.current) return null
      const coord = seriesRef.current.priceToCoordinate(price)
      return coord == null ? null : (coord as number)
    },
    [seriesRef]
  )

  const updateOverlayPositions = useCallback(() => {
    const sync = (
      lineRef: React.MutableRefObject<IPriceLine | null>,
      setter: (value: OverlayPos | null) => void
    ) => {
      if (!lineRef.current) {
        setter(null)
        return
      }
      const price = lineRef.current.options().price
      const y = priceToY(price)
      if (y == null) {
        setter(null)
        return
      }
      setter({ y, price })
    }

    sync(entryLineRef, setEntryOverlay)
    sync(tpLineRef, setTpOverlay)
    sync(slLineRef, setSlOverlay)
  }, [priceToY])

  const getDirectionForPrice = useCallback(
    (nextPrice: number): 'above' | 'below' => {
      if (!symbol) return 'above'
      const cached = MarketDataManager.getInstance().getCachedData(symbol, optionExchange)
      const ltp = cached?.data?.ltp
      if (!ltp || ltp <= 0) return 'above'
      return nextPrice >= ltp ? 'above' : 'below'
    },
    [symbol, optionExchange]
  )

  const ensureApiKey = useCallback(async (): Promise<string | null> => {
    if (apiKey) return apiKey
    try {
      const resp = await fetch('/api/websocket/apikey', { credentials: 'include' })
      const data = await resp.json()
      if (data.status === 'success' && data.api_key) {
        setApiKey(data.api_key)
        return data.api_key
      }
    } catch (err) {
      console.error('[Scalping] Failed to fetch API key:', err)
    }
    console.warn('[Scalping] No API key available — generate one at /apikey')
    return null
  }, [apiKey, setApiKey])

  const placeLimitFromChart = useCallback(
    async (entryPrice: number, action: OrderAction): Promise<boolean> => {
      if (!symbol) return false
      if (placingLimitRef.current) return false
      placingLimitRef.current = true

      try {
        if (paperMode) {
          const resolvedEntry = await resolveEntryPrice({
            symbol,
            exchange: optionExchange,
            preferredPrice: entryPrice,
          })
          if (resolvedEntry <= 0) return false
          const existingVirtual = getTPSLForSymbol(symbol)
          if (existingVirtual) removeVirtualTPSL(existingVirtual.id)
          setVirtualTPSL(
            buildVirtualPosition({
              symbol,
              exchange: optionExchange,
              side,
              action,
              entryPrice: resolvedEntry,
              quantity: quantity * lotSize,
              tpPoints,
              slPoints,
            })
          )
          incrementTradeCount()
          setPendingEntryAction(null)
          setLimitPrice(null)
          return true
        }

        const key = await ensureApiKey()
        if (!key) return false

        const order: PlaceOrderRequest = {
          apikey: key,
          strategy: 'Scalping',
          exchange: optionExchange,
          symbol,
          action,
          quantity: quantity * lotSize,
          pricetype: 'LIMIT',
          product,
          price: entryPrice,
          trigger_price: 0,
          disclosed_quantity: 0,
        }

        const res = await tradingApi.placeOrder(order)
        if (res.status !== 'success') {
          console.error('[Scalping] LIMIT order rejected:', res)
          return false
        }

        const resolvedEntry = await resolveEntryPrice({
          symbol,
          exchange: optionExchange,
          preferredPrice: entryPrice,
          apiKey: key,
        })
        if (resolvedEntry > 0) {
          const existingVirtual = getTPSLForSymbol(symbol)
          if (existingVirtual) removeVirtualTPSL(existingVirtual.id)
          setVirtualTPSL(
            buildVirtualPosition({
              symbol,
              exchange: optionExchange,
              side,
              action,
              entryPrice: resolvedEntry,
              quantity: quantity * lotSize,
              tpPoints,
              slPoints,
            })
          )
        }
        incrementTradeCount()
        setPendingEntryAction(null)
        setLimitPrice(null)
        return true
      } catch (err) {
        console.error('[Scalping] LIMIT order failed:', err)
        return false
      } finally {
        placingLimitRef.current = false
      }
    },
    [
      symbol,
      paperMode,
      optionExchange,
      side,
      quantity,
      lotSize,
      tpPoints,
      slPoints,
      product,
      ensureApiKey,
      getTPSLForSymbol,
      removeVirtualTPSL,
      setVirtualTPSL,
      incrementTradeCount,
      setPendingEntryAction,
      setLimitPrice,
    ]
  )

  // Crosshair tracking line for fresh chart placement.
  useEffect(() => {
    const chart = chartRef.current
    const series = seriesRef.current
    if (!chart || !series) return

    const showTracking =
      isActive &&
      isLimitOrTrigger &&
      !activeTPSL &&
      !activeTrigger &&
      limitPrice == null

    if (!showTracking) {
      removeLine(trackingLineRef, trackingLineSeriesRef)
      return
    }

    const handler = (param: MouseEventParams) => {
      if (!param.point) {
        removeLine(trackingLineRef, trackingLineSeriesRef)
        return
      }
      const price = series.coordinateToPrice(param.point.y)
      if (price == null) {
        removeLine(trackingLineRef, trackingLineSeriesRef)
        return
      }
      const rounded = roundToTick(price as number)
      upsertLine(
        trackingLineRef,
        trackingLineSeriesRef,
        rounded,
        '#a1a1aa',
        LineStyle.Dashed,
        1,
        rounded.toFixed(2)
      )
    }

    chart.subscribeCrosshairMove(handler)
    return () => {
      chart.unsubscribeCrosshairMove(handler)
      removeLine(trackingLineRef, trackingLineSeriesRef)
    }
  }, [
    chartRef,
    seriesRef,
    isActive,
    isLimitOrTrigger,
    activeTPSL,
    activeTrigger,
    limitPrice,
    removeLine,
    upsertLine,
  ])

  // Chart click placement for LIMIT/TRIGGER lines.
  useEffect(() => {
    const chart = chartRef.current
    const series = seriesRef.current
    if (!chart || !series || !symbol) return
    if (!isActive || !isLimitOrTrigger) return

    const handler = (param: MouseEventParams) => {
      if (!param.point) return
      const price = series.coordinateToPrice(param.point.y)
      if (price == null) return
      const rounded = roundToTick(price as number)

      if (orderType === 'TRIGGER') {
        const action = pendingEntryAction ?? activeTrigger?.action
        if (!action) {
          console.warn('[Scalping] Select BUY/SELL first, then click chart to place TRIGGER line')
          return
        }

        const direction = getDirectionForPrice(rounded)
        if (activeTrigger) {
          updateTriggerOrder(activeTrigger.id, {
            triggerPrice: rounded,
            action,
            direction,
            quantity: quantity * lotSize,
            tpPoints,
            slPoints,
          })
        } else {
          addTriggerOrder({
            id: `trigger-${Date.now()}`,
            symbol,
            exchange: optionExchange,
            side,
            action,
            triggerPrice: rounded,
            direction,
            quantity: quantity * lotSize,
            tpPoints,
            slPoints,
            createdAt: Date.now(),
          })
        }

        setLimitPrice(rounded)
        setPendingEntryAction(null)
      } else {
        // LIMIT
        if (!pendingEntryAction) {
          console.warn('[Scalping] Select BUY/SELL first, then click chart to place LIMIT line')
          return
        }
        if (activeTPSL) return
        setLimitPrice(rounded)
        void placeLimitFromChart(rounded, pendingEntryAction)
      }

      removeLine(trackingLineRef, trackingLineSeriesRef)
      updateOverlayPositions()
    }

    chart.subscribeClick(handler)
    return () => {
      chart.unsubscribeClick(handler)
    }
  }, [
    chartRef,
    seriesRef,
    symbol,
    isActive,
    isLimitOrTrigger,
    orderType,
    pendingEntryAction,
    limitPrice,
    activeTPSL,
    activeTrigger,
    quantity,
    lotSize,
    tpPoints,
    slPoints,
    optionExchange,
    side,
    addTriggerOrder,
    updateTriggerOrder,
    setLimitPrice,
    setPendingEntryAction,
    getDirectionForPrice,
    placeLimitFromChart,
    removeLine,
    updateOverlayPositions,
  ])

  // Main line drawing sync.
  useEffect(() => {
    if (!seriesRef.current) return

    const clearEntrySet = () => {
      removeLine(entryLineRef, entryLineSeriesRef)
      removeLine(tpLineRef, tpLineSeriesRef)
      removeLine(slLineRef, slLineSeriesRef)
      setEntryOverlay(null)
      setTpOverlay(null)
      setSlOverlay(null)
    }

    if (activeTPSL) {
      const entryColor = activeTPSL.action === 'BUY' ? '#00ff88' : '#ff4560'
      upsertLine(
        entryLineRef,
        entryLineSeriesRef,
        roundToTick(activeTPSL.entryPrice),
        entryColor,
        LineStyle.Dotted,
        2,
        `${activeTPSL.action} @ ${activeTPSL.entryPrice.toFixed(2)}`
      )

      if (activeTPSL.tpPrice != null) {
        upsertLine(
          tpLineRef,
          tpLineSeriesRef,
          roundToTick(activeTPSL.tpPrice),
          '#00ff88',
          LineStyle.Dashed,
          1,
          `TP @ ${activeTPSL.tpPrice.toFixed(2)}`
        )
      } else {
        removeLine(tpLineRef, tpLineSeriesRef)
      }

      if (activeTPSL.slPrice != null) {
        upsertLine(
          slLineRef,
          slLineSeriesRef,
          roundToTick(activeTPSL.slPrice),
          '#ff4560',
          LineStyle.Dashed,
          1,
          `SL @ ${activeTPSL.slPrice.toFixed(2)}`
        )
      } else {
        removeLine(slLineRef, slLineSeriesRef)
      }

      updateOverlayPositions()
      return
    }

    if (activeTrigger) {
      const { tpPrice, slPrice } = deriveTriggerPrices(activeTrigger)
      const entry = roundToTick(activeTrigger.triggerPrice)
      upsertLine(
        entryLineRef,
        entryLineSeriesRef,
        entry,
        '#ffa500',
        LineStyle.Dashed,
        2,
        `TRIGGER ${activeTrigger.action} @ ${entry.toFixed(2)}`
      )

      if (tpPrice != null) {
        upsertLine(tpLineRef, tpLineSeriesRef, tpPrice, '#00ff88', LineStyle.Dashed, 1, `TP @ ${tpPrice.toFixed(2)}`)
      } else {
        removeLine(tpLineRef, tpLineSeriesRef)
      }

      if (slPrice != null) {
        upsertLine(slLineRef, slLineSeriesRef, slPrice, '#ff4560', LineStyle.Dashed, 1, `SL @ ${slPrice.toFixed(2)}`)
      } else {
        removeLine(slLineRef, slLineSeriesRef)
      }

      updateOverlayPositions()
      return
    }

    if (isActive && isLimitOrTrigger && limitPrice != null) {
      const action = pendingAction
      const entry = roundToTick(limitPrice)
      const lineColor = orderType === 'LIMIT' ? '#06b6d4' : '#ffa500'
      upsertLine(
        entryLineRef,
        entryLineSeriesRef,
        entry,
        lineColor,
        LineStyle.Dashed,
        2,
        `${orderType} ${action} @ ${entry.toFixed(2)}`
      )

      if (tpPoints > 0) {
        const tp = roundToTick(entry + (action === 'BUY' ? tpPoints : -tpPoints))
        upsertLine(tpLineRef, tpLineSeriesRef, tp, '#00ff88', LineStyle.Dashed, 1, `TP @ ${tp.toFixed(2)}`)
      } else {
        removeLine(tpLineRef, tpLineSeriesRef)
      }

      if (slPoints > 0) {
        const sl = roundToTick(entry + (action === 'BUY' ? -slPoints : slPoints))
        upsertLine(slLineRef, slLineSeriesRef, sl, '#ff4560', LineStyle.Dashed, 1, `SL @ ${sl.toFixed(2)}`)
      } else {
        removeLine(slLineRef, slLineSeriesRef)
      }

      updateOverlayPositions()
      return
    }

    clearEntrySet()
  }, [
    seriesRef,
    activeTPSL,
    activeTrigger,
    isActive,
    isLimitOrTrigger,
    limitPrice,
    pendingAction,
    orderType,
    tpPoints,
    slPoints,
    removeLine,
    upsertLine,
    updateOverlayPositions,
  ])

  // Keep entry line title synced with live PnL ticks without redrawing lines.
  useEffect(() => {
    if (!SHOW_NATIVE_LINE_TITLES) return
    if (!activeTPSL || !entryLineRef.current) return
    entryLineRef.current.applyOptions({ title: activeEntryTitle })
  }, [activeTPSL, activeEntryTitle])

  // Keep overlay labels aligned while zooming/panning.
  useEffect(() => {
    const chart = chartRef.current
    if (!chart) return

    const sync = () => updateOverlayPositions()
    chart.subscribeCrosshairMove(sync)
    const ts = chart.timeScale()
    ts.subscribeVisibleLogicalRangeChange(sync)

    return () => {
      chart.unsubscribeCrosshairMove(sync)
      ts.unsubscribeVisibleLogicalRangeChange(sync)
    }
  }, [chartRef, updateOverlayPositions])

  // Keep overlay labels glued to their price lines during autoscale/data updates.
  useEffect(() => {
    const container = containerRef.current
    if (!container) return

    const sync = () => {
      if (!entryLineRef.current && !tpLineRef.current && !slLineRef.current) return
      updateOverlayPositions()
    }

    const intervalId = window.setInterval(sync, 100)
    container.addEventListener('wheel', sync, { passive: true })
    container.addEventListener('mousemove', sync)
    window.addEventListener('resize', sync)

    return () => {
      window.clearInterval(intervalId)
      container.removeEventListener('wheel', sync)
      container.removeEventListener('mousemove', sync)
      window.removeEventListener('resize', sync)
    }
  }, [containerRef, updateOverlayPositions])

  // Guard against stale pending lines after an order/position is fully cleared.
  useEffect(() => {
    if (activeTPSL || activeTrigger) return
    if (pendingEntryAction != null) return
    if (limitPrice == null && !entryLineRef.current && !tpLineRef.current && !slLineRef.current) return

    removeLine(entryLineRef, entryLineSeriesRef)
    removeLine(tpLineRef, tpLineSeriesRef)
    removeLine(slLineRef, slLineSeriesRef)
    setEntryOverlay(null)
    setTpOverlay(null)
    setSlOverlay(null)
    setLimitPrice(null)
  }, [activeTPSL, activeTrigger, pendingEntryAction, limitPrice, removeLine, setLimitPrice])

  // Cleanup on symbol changes.
  useEffect(() => {
    void symbol
    return () => {
      removeLine(trackingLineRef, trackingLineSeriesRef)
      removeLine(entryLineRef, entryLineSeriesRef)
      removeLine(tpLineRef, tpLineSeriesRef)
      removeLine(slLineRef, slLineSeriesRef)
      setEntryOverlay(null)
      setTpOverlay(null)
      setSlOverlay(null)
    }
  }, [symbol, removeLine])

  // Switching to MARKET clears only pending chart lines.
  useEffect(() => {
    if (orderType !== 'MARKET') return
    if (activeTPSL || activeTrigger) return

    removeLine(trackingLineRef, trackingLineSeriesRef)
    removeLine(entryLineRef, entryLineSeriesRef)
    removeLine(tpLineRef, tpLineSeriesRef)
    removeLine(slLineRef, slLineSeriesRef)
    setEntryOverlay(null)
    setTpOverlay(null)
    setSlOverlay(null)
    setLimitPrice(null)
    setPendingEntryAction(null)
  }, [orderType, activeTPSL, activeTrigger, removeLine, setLimitPrice, setPendingEntryAction])

  const handleDragStart = useCallback(
    (type: DragType, e: React.MouseEvent) => {
      e.preventDefault()
      e.stopPropagation()

      if (!currentSource) return

      const lineRef = type === 'entry' ? entryLineRef : type === 'tp' ? tpLineRef : slLineRef
      if (!lineRef.current) return

      dragRef.current = {
        type,
        source: currentSource,
      }

      const onMouseMove = (ev: MouseEvent) => {
        const drag = dragRef.current
        const series = seriesRef.current
        const container = containerRef.current
        if (!drag || !series || !container) return

        const rect = container.getBoundingClientRect()
        const yCoord = ev.clientY - rect.top
        const movedPrice = series.coordinateToPrice(yCoord)
        if (movedPrice == null) return

        const rounded = roundToTick(movedPrice as number)
        const movingRef = drag.type === 'entry' ? entryLineRef : drag.type === 'tp' ? tpLineRef : slLineRef
        if (!movingRef.current) return

        let title = ''
        if (drag.type === 'entry') {
          if (drag.source === 'trigger') {
            const action = activeTrigger?.action ?? 'BUY'
            title = `TRIGGER ${action} @ ${rounded.toFixed(2)}`
          } else if (drag.source === 'pending') {
            title = `${orderType} ${pendingAction} @ ${rounded.toFixed(2)}`
          } else if (drag.source === 'position') {
            const action = activeTPSL?.action ?? pendingAction
            title = `${action} @ ${rounded.toFixed(2)}`
          } else {
            title = movingRef.current.options().title ?? rounded.toFixed(2)
          }
        } else if (drag.type === 'tp') {
          title = `TP @ ${rounded.toFixed(2)}`
        } else {
          title = `SL @ ${rounded.toFixed(2)}`
        }

        movingRef.current.applyOptions({
          price: rounded,
          title: SHOW_NATIVE_LINE_TITLES ? title : '',
        })

        // Keep TP/SL lines and labels visually in sync while entry is being dragged.
        if (drag.type === 'entry') {
          const sourceAction =
            drag.source === 'trigger'
              ? (activeTrigger?.action ?? pendingAction)
              : drag.source === 'position'
                ? (activeTPSL?.action ?? pendingAction)
                : pendingAction
          const sourceTpPoints =
            drag.source === 'trigger'
              ? (activeTrigger?.tpPoints ?? 0)
              : drag.source === 'position'
                ? (activeTPSL?.tpPoints ?? 0)
                : tpPoints
          const sourceSlPoints =
            drag.source === 'trigger'
              ? (activeTrigger?.slPoints ?? 0)
              : drag.source === 'position'
                ? (activeTPSL?.slPoints ?? 0)
                : slPoints

          if (sourceTpPoints > 0 && tpLineRef.current) {
            const tpPrice = roundToTick(rounded + (sourceAction === 'BUY' ? sourceTpPoints : -sourceTpPoints))
            tpLineRef.current.applyOptions({
              price: tpPrice,
              title: SHOW_NATIVE_LINE_TITLES ? `TP @ ${tpPrice.toFixed(2)}` : '',
            })
          }

          if (sourceSlPoints > 0 && slLineRef.current) {
            const slPrice = roundToTick(rounded + (sourceAction === 'BUY' ? -sourceSlPoints : sourceSlPoints))
            slLineRef.current.applyOptions({
              price: slPrice,
              title: SHOW_NATIVE_LINE_TITLES ? `SL @ ${slPrice.toFixed(2)}` : '',
            })
          }
        }

        updateOverlayPositions()
      }

      const onMouseUp = () => {
        const drag = dragRef.current
        if (!drag) return

        const movingRef = drag.type === 'entry' ? entryLineRef : drag.type === 'tp' ? tpLineRef : slLineRef
        const finalPrice = movingRef.current?.options().price

        if (finalPrice != null) {
          if (drag.source === 'position' && activeTPSL) {
            const updates =
              drag.type === 'entry'
                ? {
                    entryPrice: finalPrice,
                    tpPrice:
                      activeTPSL.tpPrice != null
                        ? roundToTick(activeTPSL.tpPrice + (finalPrice - activeTPSL.entryPrice))
                        : null,
                    slPrice:
                      activeTPSL.slPrice != null
                        ? roundToTick(activeTPSL.slPrice + (finalPrice - activeTPSL.entryPrice))
                        : null,
                  }
                : drag.type === 'tp'
                  ? {
                      tpPrice: finalPrice,
                      tpPoints: formatPts(Math.abs(finalPrice - activeTPSL.entryPrice)),
                    }
                  : drag.type === 'sl'
                    ? {
                        slPrice: finalPrice,
                        slPoints: formatPts(Math.abs(activeTPSL.entryPrice - finalPrice)),
                      }
                    : null
            if (updates) updateVirtualTPSL(activeTPSL.id, updates)
          }

          if (drag.source === 'trigger' && activeTrigger) {
            if (drag.type === 'entry') {
              updateTriggerOrder(activeTrigger.id, {
                triggerPrice: finalPrice,
                direction: getDirectionForPrice(finalPrice),
              })
              setLimitPrice(finalPrice)
            }
            if (drag.type === 'tp') {
              updateTriggerOrder(activeTrigger.id, {
                tpPoints: formatPts(Math.abs(finalPrice - activeTrigger.triggerPrice)),
              })
            }
            if (drag.type === 'sl') {
              updateTriggerOrder(activeTrigger.id, {
                slPoints: formatPts(Math.abs(activeTrigger.triggerPrice - finalPrice)),
              })
            }
          }

          if (drag.source === 'pending') {
            const entryPrice = entryLineRef.current?.options().price ?? limitPrice ?? finalPrice
            if (drag.type === 'entry') setLimitPrice(finalPrice)
            if (drag.type === 'tp') setTpPoints(formatPts(Math.abs(finalPrice - entryPrice)))
            if (drag.type === 'sl') setSlPoints(formatPts(Math.abs(entryPrice - finalPrice)))
          }
        }

        updateOverlayPositions()
        dragRef.current = null
        document.removeEventListener('mousemove', onMouseMove)
        document.removeEventListener('mouseup', onMouseUp)
      }

      document.addEventListener('mousemove', onMouseMove)
      document.addEventListener('mouseup', onMouseUp)
    },
    [
      currentSource,
      seriesRef,
      containerRef,
      activeTPSL,
      activeTrigger,
      pendingAction,
      orderType,
      limitPrice,
      tpPoints,
      slPoints,
      updateVirtualTPSL,
      updateTriggerOrder,
      getDirectionForPrice,
      updateOverlayPositions,
      setLimitPrice,
      setTpPoints,
      setSlPoints,
    ]
  )

  const handleRemoveEntry = useCallback(() => {
    removeLine(entryLineRef, entryLineSeriesRef)
    removeLine(tpLineRef, tpLineSeriesRef)
    removeLine(slLineRef, slLineSeriesRef)
    setEntryOverlay(null)
    setTpOverlay(null)
    setSlOverlay(null)

    if (activeTPSL) {
      removeVirtualTPSL(activeTPSL.id)
    }
    if (activeTrigger) {
      removeTriggerOrder(activeTrigger.id)
    }

    setLimitPrice(null)
    setPendingEntryAction(null)
  }, [
    activeTPSL,
    activeTrigger,
    removeLine,
    removeVirtualTPSL,
    removeTriggerOrder,
    setLimitPrice,
    setPendingEntryAction,
  ])

  const handleRemoveOverlay = useCallback(
    (type: 'tp' | 'sl') => {
      const lineRef = type === 'tp' ? tpLineRef : slLineRef
      const lineSeriesRef = type === 'tp' ? tpLineSeriesRef : slLineSeriesRef
      removeLine(lineRef, lineSeriesRef)
      if (type === 'tp') setTpOverlay(null)
      else setSlOverlay(null)

      if (activeTPSL) {
        updateVirtualTPSL(activeTPSL.id, type === 'tp' ? { tpPrice: null, tpPoints: 0 } : { slPrice: null, slPoints: 0 })
        return
      }

      if (activeTrigger) {
        updateTriggerOrder(activeTrigger.id, type === 'tp' ? { tpPoints: 0 } : { slPoints: 0 })
        return
      }

      if (type === 'tp') setTpPoints(0)
      else setSlPoints(0)
    },
    [
      activeTPSL,
      activeTrigger,
      removeLine,
      setTpPoints,
      setSlPoints,
      updateVirtualTPSL,
      updateTriggerOrder,
    ]
  )

  const handleClosePosition = useCallback(async () => {
    if (!activeTPSL || isClosingPosition) return

    setIsClosingPosition(true)
    try {
      if (paperMode) {
        console.log(`[Paper] Close ${activeTPSL.action} ${activeTPSL.symbol}`)
      } else {
        const response = await tradingApi.closePosition(
          activeTPSL.symbol,
          activeTPSL.exchange,
          product
        )
        if (response.status !== 'success') {
          console.error('[Scalping] Close position rejected:', response)
          return
        }
      }

      removeVirtualTPSL(activeTPSL.id)
      removeLine(entryLineRef, entryLineSeriesRef)
      removeLine(tpLineRef, tpLineSeriesRef)
      removeLine(slLineRef, slLineSeriesRef)
      setEntryOverlay(null)
      setTpOverlay(null)
      setSlOverlay(null)
    } catch (err) {
      console.error('[Scalping] Close position failed:', err)
    } finally {
      setIsClosingPosition(false)
    }
  }, [activeTPSL, isClosingPosition, paperMode, product, removeVirtualTPSL, removeLine])

  if (!containerRef.current) return null

  const entryIsPosition = !!activeTPSL
  const entryColor = entryIsPosition
    ? activeTPSL.action === 'BUY'
      ? 'green'
      : 'red'
    : orderType === 'LIMIT'
      ? 'cyan'
      : 'orange'

  const entryLabel = entryIsPosition
    ? `${activeTPSL.action} @ ${entryOverlay?.price.toFixed(2) ?? '--'}`
    : activeTrigger
      ? `TRIGGER ${activeTrigger.action} @ ${entryOverlay?.price.toFixed(2) ?? '--'}`
      : `${orderType} ${pendingAction} @ ${entryOverlay?.price.toFixed(2) ?? '--'}`

  const entryLabelClass = {
    green: 'bg-green-500/15 text-green-400 border-green-500/30',
    red: 'bg-red-500/15 text-red-400 border-red-500/30',
    cyan: 'bg-cyan-500/15 text-cyan-400 border-cyan-500/30',
    orange: 'bg-orange-500/15 text-orange-400 border-orange-500/30',
  }[entryColor]

  const entryLineClass = {
    green: 'bg-green-400/50',
    red: 'bg-red-400/50',
    cyan: 'bg-cyan-400/50',
    orange: 'bg-orange-400/50',
  }[entryColor]

  const entryDragEnabled = currentSource !== null

  return (
    <>
      {entryOverlay && (
        <div className="absolute left-0 right-0 z-[14] pointer-events-none" style={{ top: entryOverlay.y }}>
          <div className={cn('absolute left-0 right-0 top-0 h-px -translate-y-1/2', entryLineClass)} />

          <div
            className={cn('absolute left-0 right-0 -top-3 h-6 pointer-events-auto', entryDragEnabled ? 'cursor-ns-resize' : 'cursor-default')}
            onMouseDown={entryDragEnabled ? (e) => handleDragStart('entry', e) : undefined}
          />

          <div className="absolute right-[62px] top-0 -translate-y-1/2 z-[15] flex items-center gap-1 pointer-events-auto">
            <span className={cn('text-[10px] font-mono px-1.5 py-0.5 rounded-sm border', entryLabelClass)}>{entryLabel}</span>
            {entryIsPosition && livePnl && (
              <span
                className={cn(
                  'text-[10px] font-mono px-1.5 py-0.5 rounded-sm border',
                  livePnl.pnl >= 0
                    ? 'bg-green-500/15 text-green-400 border-green-500/30'
                    : 'bg-red-500/15 text-red-400 border-red-500/30'
                )}
              >
                LTP {livePnl.ltp.toFixed(2)} | PnL {livePnl.pnl >= 0 ? '+' : ''}{livePnl.pnl.toFixed(2)}
              </span>
            )}
            {entryIsPosition && (
              <button
                type="button"
                className="text-[10px] font-semibold h-5 px-1.5 rounded-sm border border-destructive/40 text-destructive/80 hover:text-destructive hover:bg-destructive/10 disabled:opacity-50"
                onClick={(e) => {
                  e.stopPropagation()
                  void handleClosePosition()
                }}
                disabled={isClosingPosition}
                title="Close position at market"
              >
                {isClosingPosition ? '...' : 'Close'}
              </button>
            )}
            <button
              type="button"
              className="text-[11px] font-black w-5 h-5 flex items-center justify-center rounded-sm border border-border/70 bg-card/95 text-foreground/90 hover:text-destructive hover:bg-destructive/10 shadow-sm"
              onClick={(e) => {
                e.stopPropagation()
                handleRemoveEntry()
              }}
              title={entryIsPosition ? 'Remove virtual position tracking' : 'Cancel pending order'}
            >
              ×
            </button>
          </div>
        </div>
      )}

      {tpOverlay && (
        <div className="absolute left-0 right-0 z-[14] pointer-events-none" style={{ top: tpOverlay.y }}>
          <div className="absolute left-0 right-0 top-0 h-px -translate-y-1/2 bg-green-400/50" />
          <div
            className="absolute left-0 right-0 -top-3 h-6 cursor-ns-resize pointer-events-auto"
            onMouseDown={(e) => handleDragStart('tp', e)}
          />
          <div className="absolute right-[62px] top-0 -translate-y-1/2 z-[15] flex items-center gap-1 pointer-events-auto">
            <span className="text-[10px] font-mono px-1.5 py-0.5 rounded-sm border bg-green-500/15 text-green-400 border-green-500/30">
              TP @ {tpOverlay.price.toFixed(2)}
            </span>
            <button
              type="button"
              className="text-[11px] font-black w-5 h-5 flex items-center justify-center rounded-sm border border-green-500/40 bg-card/95 text-green-300 hover:text-green-200 hover:bg-green-500/15 shadow-sm"
              onClick={(e) => {
                e.stopPropagation()
                handleRemoveOverlay('tp')
              }}
              title="Remove TP line"
            >
              ×
            </button>
          </div>
        </div>
      )}

      {slOverlay && (
        <div className="absolute left-0 right-0 z-[14] pointer-events-none" style={{ top: slOverlay.y }}>
          <div className="absolute left-0 right-0 top-0 h-px -translate-y-1/2 bg-red-400/50" />
          <div
            className="absolute left-0 right-0 -top-3 h-6 cursor-ns-resize pointer-events-auto"
            onMouseDown={(e) => handleDragStart('sl', e)}
          />
          <div className="absolute right-[62px] top-0 -translate-y-1/2 z-[15] flex items-center gap-1 pointer-events-auto">
            <span className="text-[10px] font-mono px-1.5 py-0.5 rounded-sm border bg-red-500/15 text-red-400 border-red-500/30">
              SL @ {slOverlay.price.toFixed(2)}
            </span>
            <button
              type="button"
              className="text-[11px] font-black w-5 h-5 flex items-center justify-center rounded-sm border border-red-500/40 bg-card/95 text-red-300 hover:text-red-200 hover:bg-red-500/15 shadow-sm"
              onClick={(e) => {
                e.stopPropagation()
                handleRemoveOverlay('sl')
              }}
              title="Remove SL line"
            >
              ×
            </button>
          </div>
        </div>
      )}
    </>
  )
}
