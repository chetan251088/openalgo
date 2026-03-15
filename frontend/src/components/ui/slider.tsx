import * as React from 'react'
import { cn } from '@/lib/utils'

export interface SliderProps
  extends Omit<React.InputHTMLAttributes<HTMLInputElement>, 'value' | 'defaultValue' | 'onChange'> {
  value?: number[]
  defaultValue?: number[]
  onValueChange?: (value: number[]) => void
}

export const Slider = React.forwardRef<HTMLInputElement, SliderProps>(
  ({ className, value, defaultValue, min = 0, max = 100, step = 1, onValueChange, ...props }, ref) => {
    const sliderValue = value?.[0] ?? defaultValue?.[0] ?? Number(min)

    return (
      <input
        ref={ref}
        type="range"
        className={cn(
          'h-2 w-full cursor-pointer appearance-none rounded-lg bg-muted accent-primary',
          className
        )}
        value={sliderValue}
        min={min}
        max={max}
        step={step}
        onChange={(event) => onValueChange?.([Number(event.target.value)])}
        {...props}
      />
    )
  }
)

Slider.displayName = 'Slider'
