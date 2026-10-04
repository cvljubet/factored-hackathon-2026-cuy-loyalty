import { useId } from 'react'

interface LogoProps {
  className?: string
}

export function Logo({ className = 'size-8' }: LogoProps) {
  const gradientId = useId()

  return (
    <svg viewBox="0 0 32 32" className={className} aria-hidden="true">
      <defs>
        <linearGradient id={gradientId} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#8be3d8" />
          <stop offset="100%" stopColor="#3aa8a6" />
        </linearGradient>
      </defs>
      <path
        d="M9 3h16a4 4 0 0 1 4 4v8c0 7.7-6.3 14-14 14H7a4 4 0 0 1-4-4V9a6 6 0 0 1 6-6z"
        fill={`url(#${gradientId})`}
      />
      <path d="M12 21c0-5.5 4-9.5 10-10 0 5.5-4 9.5-10 10z" fill="#12191c" />
    </svg>
  )
}
