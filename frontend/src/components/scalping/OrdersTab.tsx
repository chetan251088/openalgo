import { useQuery } from '@tanstack/react-query'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { tradingApi } from '@/api/trading'
import { useAuthStore } from '@/stores/authStore'
import { useScalpingStore } from '@/stores/scalpingStore'
import { useVirtualOrderStore } from '@/stores/virtualOrderStore'

export function OrdersTab() {
  const apiKey = useAuthStore((s) => s.apiKey)
  const optionExchange = useScalpingStore((s) => s.optionExchange)

  const virtualTPSL = useVirtualOrderStore((s) => s.virtualTPSL)
  const triggerOrders = useVirtualOrderStore((s) => s.triggerOrders)
  const removeVirtualTPSL = useVirtualOrderStore((s) => s.removeVirtualTPSL)
  const removeTriggerOrder = useVirtualOrderStore((s) => s.removeTriggerOrder)
  const clearAll = useVirtualOrderStore((s) => s.clearAll)

  // Fetch broker orders
  const { data: orderData } = useQuery({
    queryKey: ['scalping-orders', optionExchange],
    queryFn: () => tradingApi.getOrders(apiKey!),
    enabled: !!apiKey,
    refetchInterval: 5000,
  })

  const brokerOrders = (orderData?.data?.orders ?? []).filter(
    (o) =>
      o.exchange === optionExchange &&
      (o.order_status === 'open' || o.order_status === 'pending' || o.order_status === 'trigger pending')
  )

  const tpslList = Object.values(virtualTPSL)
  const triggerList = Object.values(triggerOrders)

  return (
    <div className="p-2 space-y-3 text-xs">
      {/* Broker orders */}
      <section>
        <div className="flex items-center justify-between mb-1">
          <span className="font-medium text-foreground">Broker Orders</span>
          <Badge variant="outline" className="text-[10px] h-4">{brokerOrders.length}</Badge>
        </div>
        {brokerOrders.length === 0 ? (
          <p className="text-muted-foreground">No open broker orders</p>
        ) : (
          <div className="space-y-1">
            {brokerOrders.map((o) => (
              <div
                key={o.orderid}
                className="flex items-center justify-between p-1.5 rounded bg-muted/50"
              >
                <div>
                  <span className={o.action === 'BUY' ? 'text-green-500' : 'text-red-500'}>
                    {o.action}
                  </span>{' '}
                  <span className="font-mono">{o.symbol.slice(-10)}</span>{' '}
                  <span className="text-muted-foreground">x{o.quantity}</span>
                </div>
                <div className="flex items-center gap-1">
                  <Badge variant="outline" className="text-[10px] h-4">
                    {o.order_status}
                  </Badge>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-5 px-1 text-[10px] text-destructive"
                    onClick={async () => {
                      try {
                        await tradingApi.cancelOrder(o.orderid)
                      } catch (err) {
                        console.error('Cancel failed:', err)
                      }
                    }}
                  >
                    Cancel
                  </Button>
                </div>
              </div>
            ))}
          </div>
        )}
      </section>

      {/* Virtual TP/SL */}
      <section>
        <div className="flex items-center justify-between mb-1">
          <span className="font-medium text-foreground">Virtual TP/SL</span>
          <Badge variant="outline" className="text-[10px] h-4">{tpslList.length}</Badge>
        </div>
        {tpslList.length === 0 ? (
          <p className="text-muted-foreground">No virtual TP/SL orders</p>
        ) : (
          <div className="space-y-1">
            {tpslList.map((o) => (
              <div
                key={o.id}
                className="flex items-center justify-between p-1.5 rounded bg-muted/50"
              >
                <div>
                  <span className={o.side === 'CE' ? 'text-green-500' : 'text-red-500'}>
                    {o.side}
                  </span>{' '}
                  <span className="font-mono">{o.symbol.slice(-10)}</span>
                  <div className="text-muted-foreground">
                    Entry: {o.entryPrice.toFixed(1)} |{' '}
                    {o.tpPrice !== null && (
                      <span className="text-green-500">TP: {o.tpPrice.toFixed(1)}</span>
                    )}{' '}
                    {o.slPrice !== null && (
                      <span className="text-red-500">SL: {o.slPrice.toFixed(1)}</span>
                    )}
                  </div>
                </div>
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-5 px-1 text-[10px] text-destructive"
                  onClick={() => removeVirtualTPSL(o.id)}
                >
                  Remove
                </Button>
              </div>
            ))}
          </div>
        )}
      </section>

      {/* Trigger orders */}
      <section>
        <div className="flex items-center justify-between mb-1">
          <span className="font-medium text-foreground">Trigger Orders</span>
          <Badge variant="outline" className="text-[10px] h-4">{triggerList.length}</Badge>
        </div>
        {triggerList.length === 0 ? (
          <p className="text-muted-foreground">No trigger orders</p>
        ) : (
          <div className="space-y-1">
            {triggerList.map((o) => (
              <div
                key={o.id}
                className="flex items-center justify-between p-1.5 rounded bg-muted/50"
              >
                <div>
                  <span className={o.action === 'BUY' ? 'text-green-500' : 'text-red-500'}>
                    {o.action}
                  </span>{' '}
                  <span className="font-mono">{o.symbol.slice(-10)}</span>
                  <div className="text-muted-foreground">
                    Trigger: {o.triggerPrice.toFixed(1)} ({o.direction}) | x{o.quantity}
                  </div>
                </div>
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-5 px-1 text-[10px] text-destructive"
                  onClick={() => removeTriggerOrder(o.id)}
                >
                  Remove
                </Button>
              </div>
            ))}
          </div>
        )}
      </section>

      {/* Clear all virtual orders */}
      {(tpslList.length > 0 || triggerList.length > 0) && (
        <Button
          variant="outline"
          size="sm"
          className="w-full text-xs"
          onClick={clearAll}
        >
          Clear All Virtual Orders
        </Button>
      )}
    </div>
  )
}
