import type { OptionChainResponse } from '@/types/option-chain'
import { apiClient } from './client'
import { isMultiBrokerUnifiedMode, proxyV1ByRole } from './multi-broker'

export interface ExpiryResponse {
  status: 'success' | 'error'
  data: string[]
  message?: string
}

export const optionChainApi = {
  getOptionChain: async (
    apiKey: string,
    underlying: string,
    exchange: string,
    expiryDate: string,
    strikeCount?: number
  ): Promise<OptionChainResponse> => {
    if (isMultiBrokerUnifiedMode()) {
      return proxyV1ByRole<OptionChainResponse>('feed', 'optionchain', {
        apikey: apiKey,
        underlying,
        exchange,
        expiry_date: expiryDate,
        strike_count: strikeCount ?? 20,
      })
    }
    const response = await apiClient.post<OptionChainResponse>('/optionchain', {
      apikey: apiKey,
      underlying,
      exchange,
      expiry_date: expiryDate,
      strike_count: strikeCount ?? 20,
    })
    return response.data
  },

  getExpiries: async (
    apiKey: string,
    symbol: string,
    exchange: string,
    instrumenttype: string = 'options'
  ): Promise<ExpiryResponse> => {
    if (isMultiBrokerUnifiedMode()) {
      return proxyV1ByRole<ExpiryResponse>('feed', 'expiry', {
        apikey: apiKey,
        symbol,
        exchange,
        instrumenttype,
      })
    }
    const response = await apiClient.post<ExpiryResponse>('/expiry', {
      apikey: apiKey,
      symbol,
      exchange,
      instrumenttype,
    })
    return response.data
  },
}
