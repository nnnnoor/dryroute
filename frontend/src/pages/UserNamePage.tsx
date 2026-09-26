import { useState } from 'react'
import type { FormEvent } from 'react'
import { ArrowLeft, ArrowRight } from 'lucide-react'
import neutralCharacter from '../assets/Neutral Icon.png'

export default function UserNamePage() {
  const [name, setName] = useState(() => {
    try {
      return sessionStorage.getItem('dryroute-user-name') ?? ''
    } catch {
      return ''
    }
  })
  const [error, setError] = useState('')

  function handleContinue(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const trimmedName = name.trim()
    if (!trimmedName) {
      setError('Please enter the name you would like us to use.')
      return
    }
    try {
      sessionStorage.setItem('dryroute-user-name', trimmedName)
    } catch {
      setError('Your name could not be saved. Please allow site storage and try again.')
      return
    }
    window.location.assign('/setup')
  }

  return (
    <div className="min-h-svh sm:flex sm:flex-col sm:items-center sm:justify-center sm:gap-5 sm:bg-[radial-gradient(ellipse_at_top,#23435c,#091321_70%)] sm:px-6 sm:py-10">
      <p className="hidden text-center text-sm font-medium tracking-wide text-slate-300 sm:block">
        iPhone 17 <span className="text-slate-500">/</span> Preview
      </p>
      <div className="relative w-full sm:w-[422px] sm:shrink-0 sm:rounded-[62px] sm:border sm:border-white/25 sm:bg-[#17191c] sm:p-[9px] sm:shadow-[0_30px_80px_rgb(0_0_0/55%),inset_0_0_0_2px_#414347]">
        <div aria-hidden="true" className="absolute top-[155px] -left-[4px] hidden h-8 w-[3px] rounded-l bg-[#42464b] sm:block" />
        <div aria-hidden="true" className="absolute top-[211px] -left-[4px] hidden h-[62px] w-[3px] rounded-l bg-[#42464b] sm:block" />
        <div aria-hidden="true" className="absolute top-[286px] -left-[4px] hidden h-[62px] w-[3px] rounded-l bg-[#42464b] sm:block" />
        <div aria-hidden="true" className="absolute top-[235px] -right-[4px] hidden h-[88px] w-[3px] rounded-r bg-[#42464b] sm:block" />
        <div className="relative overflow-hidden sm:rounded-[52px]">
          <div aria-hidden="true" className="pointer-events-none absolute inset-x-0 top-0 z-20 hidden h-14 bg-[#f4f9fc] text-route-navy sm:block">
            <span className="absolute top-[19px] left-8 text-[15px] font-semibold">9:41</span>
            <div className="absolute top-3 left-1/2 h-[31px] w-[112px] -translate-x-1/2 rounded-full bg-black" />
            <span className="absolute top-[22px] right-8 text-xs font-semibold">100%</span>
          </div>
          <main aria-labelledby="name-heading" className="flex min-h-svh flex-col bg-[#f4f9fc] px-7 pt-[max(24px,env(safe-area-inset-top))] pb-[max(28px,env(safe-area-inset-bottom))] text-route-navy sm:h-[874px] sm:min-h-0 
sm:overflow-y-auto sm:[scrollbar-width:none] sm:[&::-webkit-scrollbar]:hidden
 sm:pt-[68px] sm:pb-10">
            <header className="flex items-center justify-between">
              <a href="/" aria-label="Back to welcome" className="flex size-11 items-center justify-center rounded-full border border-[#dce7ee] bg-white hover:bg-sky-50 focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-route-navy">
                <ArrowLeft aria-hidden="true" className="size-5" />
              </a>
              <p className="text-xl font-bold tracking-tight"><span className="text-route-gold">Dry</span>Route</p>
              <span aria-hidden="true" className="size-11" />
            </header>

            <div className="mt-7 flex items-center justify-center gap-2" aria-label="Step 1 of 2: Your name">
              <span className="h-1.5 w-9 rounded-full bg-route-gold" />
              <span className="h-1.5 w-9 rounded-full bg-[#dce7ee]" />
            </div>

            <div className="relative mx-auto mt-7 flex w-full max-w-[290px] shrink-0 items-center justify-center">
              <div aria-hidden="true" className="absolute inset-x-2 top-5 aspect-square rounded-full bg-[radial-gradient(circle,#d5efff,transparent_72%)]" />
              <img src={neutralCharacter} alt="A friendly blue raindrop with a backpack holding a route map" className="relative h-[260px] w-[260px] object-contain drop-shadow-[0_12px_16px_rgba(0,35,71,0.08)]" />
            </div>

            <div className="mt-5 text-center">
              <p className="text-[11px] font-bold tracking-[0.18em] text-[#92701d] uppercase">Your journey starts here</p>
              <h1 id="name-heading" className="mt-3 text-[32px] leading-[1.15] font-bold tracking-tight">What should we<br />call you?</h1>
              <p id="name-description" className="mx-auto mt-3 max-w-[285px] text-sm leading-6 text-[#5c7184]">Before we find your way, let's get to know you. A first name or nickname works!</p>
            </div>

            <form onSubmit={handleContinue} className="mt-7 flex flex-1 flex-col" noValidate>
              <label htmlFor="user-name" className="mb-2 text-sm font-bold">Your name</label>
              <input
                id="user-name"
                name="name"
                type="text"
                autoComplete="given-name"
                autoCapitalize="words"
                enterKeyHint="next"
                maxLength={60}
                required
                value={name}
                onChange={event => { setName(event.target.value); setError('') }}
                aria-invalid={Boolean(error)}
                aria-describedby={error ? 'name-description name-error' : 'name-description'}
                placeholder="e.g. Alex"
                className="min-h-14 w-full rounded-2xl border border-[#cfdee8] bg-white px-4 text-base text-route-navy shadow-sm outline-none placeholder:text-[#8497a6] focus:border-route-navy focus:ring-2 focus:ring-route-navy/15 aria-invalid:border-red-600"
              />
              {error && <p id="name-error" role="alert" className="mt-2 text-sm text-red-700">{error}</p>}
              <div className="mt-auto pt-7">
                <button type="submit" className="cursor-pointer flex min-h-14 w-full items-center justify-center gap-3 rounded-full bg-route-navy px-6 text-base font-semibold text-white shadow-[0_6px_16px_rgba(0,35,71,0.15)] hover:bg-[#0c375e] active:brightness-95 focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-route-navy">
                  Continue <ArrowRight aria-hidden="true" className="size-5" />
                </button>
                <p className="mt-3 text-center text-xs text-[#708596]">Next up: make DryRoute work for you.</p>
              </div>
            </form>
          </main>
          <div aria-hidden="true" className="pointer-events-none absolute bottom-2 left-1/2 hidden h-[5px] w-[134px] -translate-x-1/2 rounded-full bg-route-navy sm:block" />
        </div>
      </div>
    </div>
  )
}
