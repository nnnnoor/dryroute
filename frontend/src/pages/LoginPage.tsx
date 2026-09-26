import logo from '../assets/dryroute-logo.png'
import campus from '../assets/fiu-campus.png'

type LoginPageProps = {
  getStartedHref?: string
  loginHref?: string
}

const buttonClasses = 'flex min-h-[52px] items-center justify-center rounded-full border border-white/35 px-6 py-3 text-lg leading-[26px] text-white no-underline shadow-[inset_0_0_12px_rgb(255_255_255/8%)] hover:brightness-110 active:brightness-95 focus-visible:outline-3 focus-visible:outline-offset-4 focus-visible:outline-white'


function IPhoneChrome() {
  return (
    <div aria-hidden="true" className="pointer-events-none absolute inset-0 z-10 hidden select-none sm:block">
      <div className="absolute top-[19px] left-8 text-[15px] font-semibold">9:41</div>
      <div className="absolute top-3 left-1/2 h-[31px] w-[112px] -translate-x-1/2 rounded-full bg-black">
        <span className="absolute top-[11px] right-3 size-2 rounded-full bg-[#142139] ring-2 ring-[#0b0d12]" />
      </div>
      <div className="absolute top-[23px] right-7 flex items-center gap-[6px]">
        <svg width="17" height="12" viewBox="0 0 17 12" fill="currentColor">
          <rect y="8" width="3" height="4" rx=".8" />
          <rect x="4.5" y="5" width="3" height="7" rx=".8" />
          <rect x="9" y="2.5" width="3" height="9.5" rx=".8" />
          <rect x="13.5" width="3" height="12" rx=".8" />
        </svg>
        <svg width="16" height="12" viewBox="0 0 16 12" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
          <path d="M1 3a11 11 0 0 1 14 0M4 6a6 6 0 0 1 8 0M7 9a2 2 0 0 1 2 0" />
        </svg>
        <svg width="25" height="12" viewBox="0 0 25 12" fill="currentColor">
          <rect x=".5" y=".5" width="21" height="11" rx="3" fill="none" stroke="currentColor" opacity=".7" />
          <rect x="2" y="2" width="18" height="8" rx="1.5" />
          <path d="M23 4v4c2 0 2-4 0-4" opacity=".7" />
        </svg>
      </div>
      <div className="absolute bottom-2 left-1/2 h-[5px] w-[134px] -translate-x-1/2 rounded-full bg-white" />
    </div>
  )
}

export default function LoginPage({
  getStartedHref = '/signup',
  loginHref = '/login',
}: LoginPageProps) {
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
          <IPhoneChrome />
          <main
            className="@container flex min-h-svh flex-col items-center justify-between bg-cover bg-center px-4 pt-[max(12.1svh,env(safe-area-inset-top))] pb-[max(9.8svh,env(safe-area-inset-bottom))] sm:h-[874px] sm:min-h-0 sm:pt-[106px] sm:pb-[86px]"
            style={{ backgroundImage: `url(${campus})` }}
            aria-labelledby="brand-name"
          >
            <header className="w-full max-w-[370px]">
              <div className="flex items-center gap-2 max-[360px]:gap-1">
                <span className="relative block h-[86px] basis-[70px] shrink-0 overflow-hidden max-[360px]:h-[70px] max-[360px]:basis-[56px]" aria-hidden="true">
                  <img className="absolute -top-3 -left-[17px] size-[106px] max-w-none max-[360px]:-top-[10px] max-[360px]:-left-[14px] max-[360px]:size-[86px]" src={logo} alt="" />
                </span>
                <h1 id="brand-name" className="m-0 text-[clamp(36px,17.3cqw,64px)] leading-none font-bold tracking-[-1.6px] whitespace-nowrap text-route-navy">
                  <span className="text-route-gold">Dry</span>Route
                </h1>
              </div>
              <p className="mt-2 ml-[78px] text-center text-[clamp(12px,4.32cqw,16px)] leading-5 font-bold whitespace-nowrap text-white max-[360px]:ml-[60px]">
                Safer Routes . Better Days.
              </p>
            </header>
            <nav className="mt-16 flex w-full max-w-[274px] flex-col gap-[19px]" aria-label="Get started with DryRoute">
              <a className={`${buttonClasses} bg-linear-to-b from-[#00122e] to-[#031d42]`} href={getStartedHref}>
                Get Started
              </a>
              <a className={`${buttonClasses} bg-linear-to-b from-[#a97911] to-[#bb881c]`} href={loginHref}>
                Log In
              </a>
            </nav>
          </main>
        </div>
      </div>
    </div>
  )
}
