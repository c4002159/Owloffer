import { openRepositoryForStar, useOwl } from '@/lib/owl'

interface OwlLogoProps {
  size?: number
  className?: string
}

/** The owl: stern until the user stars the project, happy afterwards. Clicking it opens the repository to press Star. */
export function OwlLogo({ size = 40, className = '' }: OwlLogoProps) {
  const { happy } = useOwl()
  return (
    <button
      type="button"
      onClick={openRepositoryForStar}
      title={happy ? '谢谢你的 Star！' : '给 OwlOffer 点个 Star 吧'}
      aria-label={happy ? '猫头鹰很开心，谢谢你的 Star' : '猫头鹰在等你给项目点 Star'}
      className={`shrink-0 rounded-full transition-transform hover:scale-105 focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary ${className}`}
      style={{ width: size, height: size }}
    >
      <img src={happy ? '/owl-happy-small.png' : '/owl-default-small.png'} alt="" width={size} height={size} className="h-full w-full rounded-full" />
    </button>
  )
}
