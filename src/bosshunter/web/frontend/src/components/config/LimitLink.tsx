import { describeLimit } from '@/lib/limitReasons'

/** "已达到「…」" with a link that opens the settings page on that very setting. Nothing when the reason is not a limit. */
export function LimitLink({ reason, platform }: { reason: string; platform?: string }) {
  const info = describeLimit(reason, platform)
  if (!info) return null
  return (
    <span>
      {info.text}
      <a href={info.href} className="ml-2 font-bold text-primary underline">去调整</a>
    </span>
  )
}
