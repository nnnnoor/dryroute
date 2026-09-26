
import { useState } from 'react'
import {
  MapPin,
  Bell,
  Check,
  ArrowRight,
  ShieldCheck,
  Loader2,
} from 'lucide-react'

import logo from '../assets/dryroute-logo.png'
import campus from '../assets/fiu-campus.png'
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
          <div className="pointer-events-none absolute inset-x-0 top-0 z-20 hidden h-14 text-white sm:block">
            <span className="absolute top-[19px] left-8 text-[15px] font-semibold">
              9:41
            </span>

            <div className="absolute top-3 left-1/2 h-[31px] w-[112px] -translate-x-1/2 rounded-full bg-black" />

            <span className="absolute top-[22px] right-8 text-xs font-semibold">
              100%
            </span>
          </div>

          <main
            className="relative flex min-h-svh flex-col bg-cover bg-center px-6 pt-[max(60px,env(safe-area-inset-top))] pb-[max(35px,env(safe-area-inset-bottom))] text-white sm:h-[874px] sm:min-h-0 sm:overflow-y-auto sm:pt-[85px] sm:pb-[45px]"
            style={{ backgroundImage: `url(${campus})` }}
          >

            {/* Background overlay */}
            <div className="absolute inset-0 bg-[#00122e]/75" />

            <div className="relative z-10 flex flex-1 flex-col">

              {/* Branding */}
              <header className="flex flex-col items-center text-center">

                <img
                  src={logo}
                  alt="DryRoute logo"
                  className="h-20 w-20 object-contain"
                />

                <h1 className="mt-2 text-[38px] font-bold tracking-tight">
                  <span className="text-route-gold">Dry</span>
                  Route
                </h1>

                <p className="mt-1 text-sm font-semibold tracking-wide text-white/85">
                  Safer Routes. Better Days.
                </p>

              </header>

              {/* Intro */}
              <section className="mt-8 text-center">

                <h2 className="text-[25px] font-bold tracking-tight">
                  Let's get you moving.
                </h2>

                <p className="mx-auto mt-2 max-w-[290px] text-sm leading-6 text-white/70">
                  A few quick steps to personalize your commute
                  and help you stay ahead of the weather.
                </p>

                {/* Progress */}
                <div className="mt-5 flex items-center justify-center gap-2">
                  {[0, 1, 2].map((step) => (
                    <div
                      key={step}
                      className={`h-[5px] w-14 rounded-full transition-colors ${
                        step < completed
                          ? 'bg-route-gold'
                          : 'bg-white/20'
                      }`}
                    />
                  ))}
                </div>

                <p className="mt-2 text-xs text-white/55">
                  {completed} of 3 connected
                </p>

              </section>

              {/* Setup cards */}
              <section className="mt-7 flex flex-col gap-3">

                {/* Location */}
                <div className="rounded-[22px] border border-white/20 bg-[#061c3b]/85 p-4 shadow-lg backdrop-blur-md">

                  <div className="flex items-center gap-3">

                    <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl bg-white/10">
                      <MapPin className="h-6 w-6 text-route-gold" />
                    </div>

                    <div className="flex-1">
                      <h3 className="text-[15px] font-bold">
                        Enable Location
                      </h3>

                      <p className="mt-1 text-xs leading-5 text-white/60">
                        Find nearby flood risks and safer routes.
                      </p>
                    </div>

                    {locationStatus === 'success' && (
                      <Check className="h-5 w-5 text-green-400" />
                    )}

                  </div>

                  <button
                    type="button"
                    onClick={enableLocation}
                    disabled={locationStatus === 'loading'}
                    className="mt-4 flex min-h-11 w-full items-center justify-center gap-2 rounded-full border border-white/20 bg-white/10 text-sm font-semibold transition hover:bg-white/20 disabled:opacity-60"
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
                <div className="rounded-[22px] border border-white/20 bg-[#061c3b]/85 p-4 shadow-lg backdrop-blur-md">

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

                      <p className="mt-1 text-xs leading-5 text-white/60">
                        Plan your commute around classes and events.
                      </p>
                    </div>

                    {calendarConnected && (
                      <Check className="h-5 w-5 text-green-400" />
                    )}

                  </div>

                  <button
                    type="button"
                    onClick={connectCalendar}
                    disabled={calendarConnected}
                    className="mt-4 flex min-h-11 w-full items-center justify-center gap-2 rounded-full bg-white text-sm font-semibold text-[#081E3F] transition hover:bg-gray-100 disabled:opacity-70"
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

                  <p className="mt-2 text-center text-[10px] text-white/45">
                    Optional · Read-only calendar access
                  </p>

                </div>

                {/* Notifications */}
                <div className="rounded-[22px] border border-white/20 bg-[#061c3b]/85 p-4 shadow-lg backdrop-blur-md">

                  <div className="flex items-center gap-3">

                    <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl bg-white/10">
                      <Bell className="h-6 w-6 text-route-gold" />
                    </div>

                    <div className="flex-1">
                      <h3 className="text-[15px] font-bold">
                        Stay Ahead
                      </h3>

                      <p className="mt-1 text-xs leading-5 text-white/60">
                        Get flood alerts and commute reminders.
                      </p>
                    </div>

                    {notificationStatus === 'success' && (
                      <Check className="h-5 w-5 text-green-400" />
                    )}

                  </div>

                  <button
                    type="button"
                    onClick={enableNotifications}
                    disabled={notificationStatus === 'loading'}
                    className="mt-4 flex min-h-11 w-full items-center justify-center gap-2 rounded-full border border-white/20 bg-white/10 text-sm font-semibold transition hover:bg-white/20"
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
                  className="mt-4 rounded-xl bg-red-500/15 px-3 py-2 text-center text-xs text-red-200"
                >
                  {message}
                </p>
              )}

              {/* Continue */}
              <div className="mt-auto pt-7">

                <button
                  type="button"
                  onClick={continueToDashboard}
                  className="flex min-h-[54px] w-full items-center justify-center gap-3 rounded-full border border-white/30 bg-linear-to-b from-[#a97911] to-[#bb881c] text-lg font-bold text-white shadow-lg transition hover:brightness-110 active:brightness-95"
                >
                  Continue to DryRoute
                  <ArrowRight className="h-5 w-5" />
                </button>

                <p className="mt-4 flex items-center justify-center gap-2 text-center text-xs text-white/55">
                  <ShieldCheck className="h-4 w-4" />
                  You're in control of your permissions.
                </p>

              </div>

            </div>

          </main>

          {/* Home indicator */}
          <div className="pointer-events-none absolute bottom-2 left-1/2 z-20 hidden h-[5px] w-[134px] -translate-x-1/2 rounded-full bg-white sm:block" />

        </div>
      </div>
    </div>
  )
}
