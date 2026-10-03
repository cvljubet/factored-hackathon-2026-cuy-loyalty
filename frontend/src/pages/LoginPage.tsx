import { useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'
import { ArrowRight, CreditCard, Eye, EyeOff, Gift, Lock, Mail, Plane } from 'lucide-react'
import { Header } from '../components/Header'

const features = [
  { icon: Gift, labelKey: 'login.features.personalized' },
  { icon: Plane, labelKey: 'login.features.recommendations' },
  { icon: CreditCard, labelKey: 'login.features.benefits' },
] as const

export function LoginPage() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const [showPassword, setShowPassword] = useState(false)

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    // Mock sign-in: Cognito authentication will replace this.
    navigate('/chat')
  }

  return (
    <div className="flex min-h-dvh flex-col">
      <Header />

      <main className="relative flex-1 overflow-hidden bg-mint-50">
        {/* Decorative curved backdrop standing in for the reference's sky image */}
        <div
          aria-hidden="true"
          className="pointer-events-none absolute -left-1/3 -top-1/4 h-[150%] w-[170%] rounded-full bg-linear-to-b from-mint-100 via-mint-200 to-brand-400/70 lg:-left-1/4 lg:w-[85%]"
        />

        <div className="relative mx-auto grid max-w-6xl items-center gap-10 px-4 py-10 sm:px-8 lg:grid-cols-2 lg:gap-16 lg:py-24">
          <section>
            <h1 className="text-4xl font-semibold leading-tight tracking-tight sm:text-5xl lg:text-6xl">
              {t('login.heroTitleLine1')}
              <br />
              <span className="text-brand-600">{t('login.heroTitleLine2')}</span>
            </h1>
            <p className="mt-5 max-w-md text-lg text-slate-600 sm:text-xl">
              {t('login.heroSubtitle')}
            </p>

            <ul className="mt-8 grid max-w-lg grid-cols-3 divide-x divide-ink-900/10">
              {features.map(({ icon: Icon, labelKey }) => (
                <li key={labelKey} className="flex flex-col gap-3 px-3 first:pl-0 sm:px-5">
                  <span className="flex size-12 items-center justify-center rounded-full bg-white/70 sm:size-14">
                    <Icon className="size-6 text-ink-900" strokeWidth={1.75} />
                  </span>
                  <span className="text-sm text-slate-700 sm:text-base">{t(labelKey)}</span>
                </li>
              ))}
            </ul>
          </section>

          <section className="rounded-2xl bg-white p-6 shadow-xl shadow-ink-900/5 sm:p-12">
            <h2 className="text-3xl font-bold sm:text-4xl">{t('login.welcome')}</h2>
            <p className="mt-3 text-slate-500 sm:text-lg">{t('login.subtitle')}</p>

            <form onSubmit={handleSubmit} className="mt-10 space-y-6">
              <div>
                <label htmlFor="email" className="mb-2 block font-medium">
                  {t('login.emailLabel')}
                </label>
                <div className="flex items-center gap-3 rounded-lg border border-slate-200 px-4 focus-within:border-brand-500 focus-within:ring-2 focus-within:ring-brand-500/20">
                  <Mail className="size-5 shrink-0 text-slate-500" />
                  <input
                    id="email"
                    type="email"
                    required
                    autoComplete="email"
                    placeholder={t('login.emailPlaceholder')}
                    className="w-full py-4 outline-none placeholder:text-slate-400"
                  />
                </div>
              </div>

              <div>
                <label htmlFor="password" className="mb-2 block font-medium">
                  {t('login.passwordLabel')}
                </label>
                <div className="flex items-center gap-3 rounded-lg border border-slate-200 px-4 focus-within:border-brand-500 focus-within:ring-2 focus-within:ring-brand-500/20">
                  <Lock className="size-5 shrink-0 text-slate-500" />
                  <input
                    id="password"
                    type={showPassword ? 'text' : 'password'}
                    required
                    autoComplete="current-password"
                    placeholder={t('login.passwordPlaceholder')}
                    className="w-full py-4 outline-none placeholder:text-slate-400"
                  />
                  <button
                    type="button"
                    onClick={() => setShowPassword((shown) => !shown)}
                    className="text-slate-500 hover:text-ink-900"
                    aria-label={showPassword ? t('login.hidePassword') : t('login.showPassword')}
                  >
                    {showPassword ? <EyeOff className="size-5" /> : <Eye className="size-5" />}
                  </button>
                </div>
                <div className="mt-3 text-right">
                  <a href="#" className="text-brand-600 underline underline-offset-2 hover:text-brand-700">
                    {t('login.forgotPassword')}
                  </a>
                </div>
              </div>

              <button
                type="submit"
                className="flex w-full items-center justify-center gap-3 rounded-lg bg-brand-500 py-4 text-lg font-semibold text-white transition-colors hover:bg-brand-600"
              >
                {t('login.signIn')}
                <ArrowRight className="size-5" />
              </button>
            </form>
          </section>
        </div>
      </main>
    </div>
  )
}
