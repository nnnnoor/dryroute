
import { useState } from 'react'
import {
  MapPin,
  Bell,
  Check,
  ArrowRight,
  ShieldCheck,
  Loader2,
  ArrowLeft,
} from 'lucide-react'

import googleLogo from '../assets/image 5.png'

type GoogleCalendarProps = {
  dashboardHref?: string
  calendarConnected?: boolean
}

type PermissionStatus = 'idle' | 'loading' | 'success' | 'error'

export default function GoogleCalendar({
  dashboardHref = '/dashboard',
  calendarConnected = false,
}: GoogleCalendarProps) {
  const [locationStatus, setLocationStatus] =
    useState<PermissionStatus>('idle')

  const [notificationStatus, setNotificationStatus] =
    useState<PermissionStatus>('idle')

  const [location, setLocation] = useState<{
    latitude: number
    longitude: number
  } | null>(null)

  const [message, setMessage] = useState('')
  const [userName] = useState(() => {
    try {
      return sessionStorage.getItem('dryroute-user-name')?.trim() ?? ''
    } catch {
      return ''
    }
  })

  const enableLocation = () => {
    if (!navigator.geolocation) {
      setMessage('Location services are not supported.')
      setLocationStatus('error')
      return
    }

    setLocationStatus('loading')
    setMessage('')

    navigator.geolocation.getCurrentPosition(
      (position) => {
        const coordinates = {
          latitude: position.coords.latitude,
          longitude: position.coords.longitude,
        }

        setLocation(coordinates)
        setLocationStatus('success')
      },
      () => {
        setLocationStatus('error')
        setMessage(
          'Location access was denied. Please enable it in your browser settings.'
        )
      },
      {
        enableHighAccuracy: true,
        timeout: 15000,
        maximumAge: 60000,
      }
    )
  }

  const connectCalendar = () => {
    // Backend must implement this Google OAuth endpoint.
    window.location.assign('/api/auth/google')
  }

  const enableNotifications = async () => {
    if (!('Notification' in window)) {
      setNotificationStatus('error')
      setMessage('Notifications are not supported in this browser.')
      return
    }

    setNotificationStatus('loading')

    const permission = await Notification.requestPermission()

    if (permission === 'granted') {
      setNotificationStatus('success')
      setMessage('')
    } else {
      setNotificationStatus('error')
      setMessage('Notification permission was not granted.')
    }
  }

  const continueToDashboard = () => {
    const preferences = {
      locationEnabled: locationStatus === 'success',
      calendarConnected,
      notificationsEnabled: notificationStatus === 'success',
      coordinates: location,
    }

    sessionStorage.setItem(
      'dryroute-onboarding',
      JSON.stringify(preferences)
    )

    window.location.assign(dashboardHref)
  }

  const completed =
    Number(locationStatus === 'success') +
    Number(calendarConnected) +
    Number(notificationStatus === 'success')

  return (
    <div className="min-h-svh sm:flex sm:flex-col sm:items-center sm:justify-center sm:gap-5 sm:bg-[radial-gradient(ellipse_at_top,#23435c,#091321_70%)] sm:px-6 sm:py-10">

      <p className="hidden text-center text-sm font-medium tracking-wide text-slate-300 sm:block">
        iPhone 17 <span className="text-slate-500">/</span> Preview
      </p>

      {/* Phone frame */}
      <div className="relative w-full sm:w-[422px] sm:rounded-[62px] sm:border sm:border-white/25 sm:bg-[#17191c] sm:p-[9px] sm:shadow-[0_30px_80px_rgb(0_0_0/55%),inset_0_0_0_2px_#414347]">

        <div className="relative overflow-hidden sm:rounded-[52px]">

          {/* iPhone Dynamic Island */}
          <div className="pointer-events-none absolute inset-x-0 top-0 z-20 hidden h-14 bg-[#f4f9fc] text-route-navy sm:block">
            <span className="absolute top-[19px] left-8 text-[15px] font-semibold">
              9:41
            </span>

            <div className="absolute top-3 left-1/2 h-[31px] w-[112px] -translate-x-1/2 rounded-full bg-black" />

            <span className="absolute top-[22px] right-8 text-xs font-semibold">
              100%
            </span>
          </div>

          <main
            className="relative flex min-h-svh flex-col bg-[#f4f9fc] px-7 pt-[max(60px,env(safe-area-inset-top))] pb-[max(35px,env(safe-area-inset-bottom))] text-route-navy sm:h-[874px] sm:min-h-0 
            sm:overflow-y-auto sm:[scrollbar-width:none] sm:[&::-webkit-scrollbar]:hidden
            sm:pt-[68px] sm:pb-[45px]"
          >


            <div className="relative z-10 flex flex-1 flex-col">
              <header className="flex items-center justify-between">
                <a href="/signup" aria-label="Back to your name" className="flex size-11 items-center justify-center rounded-full border border-[#dce7ee] bg-white hover:bg-sky-50 focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-route-navy">
                  <ArrowLeft aria-hidden="true" className="size-5" />
                </a>
                <p className="text-xl font-bold tracking-tight"><span className="text-route-gold">Dry</span>Route</p>
                <span aria-hidden="true" className="size-11" />
              </header>
              <section className="mt-8 text-center" aria-labelledby="setup-heading">
                <p className="text-[11px] font-bold tracking-[0.18em] text-[#92701d] uppercase">Make yourself at home</p>
                <h1 id="setup-heading" className="mt-3 text-[32px] leading-[1.15] font-bold tracking-tight [overflow-wrap:anywhere]">
                  {userName ? `Welcome, ${userName}` : 'Welcome!'}
                </h1>
                <p className="mx-auto mt-3 max-w-[285px] text-sm leading-6 text-[#5c7184]">
                  A few quick steps to make DryRoute work for you.
                </p>
                {/* Progress */}
                <div className="mt-5 flex items-center justify-center gap-2">
                  {[0, 1, 2].map((step) => (
                    <div
                      key={step}
                      className={`h-[5px] w-14 rounded-full transition-colors ${
                        step < completed
                          ? 'bg-route-gold'
                          : 'bg-[#dce7ee]'
                      }`}
                    />
                  ))}
                </div>

                <p className="mt-2 text-xs text-[#5c7184]">
                  {completed} of 3 connected
                </p>

              </section>

              {/* Setup cards */}
              <section className="mt-7 flex flex-col gap-3">

                {/* Location */}
                <div className="rounded-[22px] border border-[#dce7ee] bg-white p-4 shadow-sm">

                  <div className="flex items-center gap-3">

                    <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl bg-[#f4f9fc]">
                      <MapPin className="h-6 w-6 text-route-gold" />
                    </div>

                    <div className="flex-1">
                      <h3 className="text-[15px] font-bold">
                        Enable Location
                      </h3>

                      <p className="mt-1 text-xs leading-5 text-[#5c7184]">
                        Find nearby flood risks and safer routes.
                      </p>
                    </div>

                    {locationStatus === 'success' && (
                      <Check className="h-5 w-5 text-green-700" />
                    )}

                  </div>

                  <button
                    type="button"
                    onClick={enableLocation}
                    disabled={locationStatus === 'loading'}
                    className="mt-4 flex min-h-11 w-full items-center justify-center gap-2 rounded-full border border-[#cfdee8] bg-[#f4f9fc] text-sm font-semibold transition hover:bg-sky-50 focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-route-navy disabled:opacity-60"
                  >
                    {locationStatus === 'loading' ? (
                      <>
                        <Loader2 className="h-4 w-4 animate-spin" />
                        Requesting Location...
                      </>
                    ) : locationStatus === 'success' ? (
                      <>
                        <Check className="h-4 w-4" />
                        Location Enabled
                      </>
                    ) : (
                      'Allow Location Access'
                    )}
                  </button>

                </div>

                {/* Google Calendar */}
                <div className="rounded-[22px] border border-[#dce7ee] bg-white p-4 shadow-sm">

                  <div className="flex items-center gap-3">

                    <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl bg-white p-2">
                      <img
                        src={googleLogo}
                        alt=""
                        className="h-full w-full object-contain"
                      />
                    </div>

                    <div className="flex-1">
                      <h3 className="text-[15px] font-bold">
                        Google Calendar
                      </h3>

                      <p className="mt-1 text-xs leading-5 text-[#5c7184]">
                        Plan your commute around classes and events.
                      </p>
                    </div>

                    {calendarConnected && (
                      <Check className="h-5 w-5 text-green-700" />
                    )}

                  </div>

                  <button
                    type="button"
                    onClick={connectCalendar}
                    disabled={calendarConnected}
                    className="mt-4 flex min-h-11 w-full items-center justify-center gap-2 rounded-full border border-[#cfdee8] bg-white text-sm font-semibold text-[#081E3F] transition hover:bg-sky-50 focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-route-navy disabled:opacity-70"
                  >
                    {calendarConnected ? (
                      <>
                        <Check className="h-4 w-4" />
                        Calendar Connected
                      </>
                    ) : (
                      <>
                        <img
                          src={googleLogo}
                          alt=""
                          className="h-5 w-5 object-contain"
                        />
                        Connect Google Calendar
                      </>
                    )}
                  </button>

                  <p className="mt-2 text-center text-[10px] text-[#5c7184]">
                    Optional · Read-only calendar access
                  </p>

                </div>

                {/* Notifications */}
                <div className="rounded-[22px] border border-[#dce7ee] bg-white p-4 shadow-sm">

                  <div className="flex items-center gap-3">

                    <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl bg-[#f4f9fc]">
                      <Bell className="h-6 w-6 text-route-gold" />
                    </div>

                    <div className="flex-1">
                      <h3 className="text-[15px] font-bold">
                        Stay Ahead
                      </h3>

                      <p className="mt-1 text-xs leading-5 text-[#5c7184]">
                        Get flood alerts and commute reminders.
                      </p>
                    </div>

                    {notificationStatus === 'success' && (
                      <Check className="h-5 w-5 text-green-700" />
                    )}

                  </div>

                  <button
                    type="button"
                    onClick={enableNotifications}
                    disabled={notificationStatus === 'loading'}
                    className="mt-4 flex min-h-11 w-full items-center justify-center gap-2 rounded-full border border-[#cfdee8] bg-[#f4f9fc] text-sm font-semibold transition hover:bg-sky-50 focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-route-navy"
                  >
                    {notificationStatus === 'success' ? (
                      <>
                        <Check className="h-4 w-4" />
                        Notifications Enabled
                      </>
                    ) : (
                      'Enable Notifications'
                    )}
                  </button>

                </div>

              </section>

              {/* Error feedback */}
              {message && (
                <p
                  role="alert"
                  className="mt-4 rounded-xl bg-red-50 px-3 py-2 text-center text-xs text-red-700"
                >
                  {message}
                </p>
              )}

              {/* Continue */}
              <div className="mt-auto pt-7">

                <button
                  type="button"
                  onClick={continueToDashboard}
                  className="cursor-pointer flex min-h-[54px] w-full items-center justify-center gap-3 rounded-full bg-route-navy text-lg font-bold text-white shadow-[0_6px_16px_rgba(0,35,71,0.15)] transition hover:bg-[#0c375e] active:brightness-95 focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-route-navy"
                >
                  Continue to DryRoute
                  <ArrowRight className="h-5 w-5" />
                </button>

                <p className="mt-4 flex items-center justify-center gap-2 text-center text-xs text-[#5c7184]">
                  <ShieldCheck className="h-4 w-4" />
                  You're in control of your permissions.
                </p>

              </div>

            </div>

          </main>

          {/* Home indicator */}
          <div className="pointer-events-none absolute bottom-2 left-1/2 z-20 hidden h-[5px] w-[134px] -translate-x-1/2 rounded-full bg-route-navy sm:block" />

        </div>
      </div>
    </div>
  )
}
