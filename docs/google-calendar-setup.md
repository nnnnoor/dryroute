# Connect Google Calendar

The setup page now starts Google OAuth on the backend. Access and refresh tokens stay in server memory; the browser receives an opaque HttpOnly session cookie. The app reads your primary calendar and never creates or edits Google events.

## Google Cloud configuration

1. In your Google Cloud project, enable **Google Calendar API**.
2. Configure Google Auth Platform branding and audience. While the app is in testing, add your Google account as a test user.
3. Add the scope `https://www.googleapis.com/auth/calendar.events.readonly` under Data Access.
4. Create an OAuth client with application type **Web application**. Add this exact authorized redirect URI: `http://localhost:8000/auth/google/callback`.
5. Set these values in `backend/.env` (do not commit it):

```dotenv
GOOGLE_CLIENT_ID=your-client-id.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=your-client-secret
GOOGLE_REDIRECT_URI=http://localhost:8000/auth/google/callback
FRONTEND_URL=http://localhost:5173
```

No Google credentials belong in the frontend. Its `frontend/.env.local` should contain:

```dotenv
VITE_API_BASE_URL=http://localhost:8000
```

Restart both servers after changing their environment files. From `backend`, run `.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000`. From `frontend`, run `npm run dev -- --port 5173`. Open `http://localhost:5173`, enter your name, and select **Connect Google Calendar** in setup. Approve read-only access, then continue to DryRoute.

Use `localhost` consistently for both servers; mixing `127.0.0.1` and `localhost` breaks same-site cookies. A `redirect_uri_mismatch` means the URI registered in Google Cloud differs from `GOOGLE_REDIRECT_URI`. Access denied while testing usually means the account is missing from the test-user list. A refused calendar request can also mean the Calendar API is not enabled.

## Try it

Add a timed event in your primary Google Calendar within the next seven days. Put a recognized FIU building such as `PC 213` in its location. Connect, allow location, and continue: the schedule displays the event; the commute service uses your saved coordinates to calculate departure and warnings. Unknown destinations still display but do not receive a route. The location permission captures your position when requested; it is not continuous background tracking.

The schedule refreshes every minute while open and has a manual refresh button. Alerts are in-app; enabling browser notification permission alone does not implement background push delivery. All-day events and secondary/shared calendars are not displayed in this version. Upcoming commute selection uses the backend's calendar lookahead setting.

**Try a demo schedule** works without Google credentials and is labeled as demo. `USE_FAKE_CALENDAR=true` preserves anonymous fixture endpoints for existing backend development/tests; a connected Google session always uses Google. Set it to `false` to require a connection for anonymous calendar/alert requests. Explicit demo selection still works. Disconnect removes local tokens and private session data; it does not revoke Google's consent grant (remove that in your Google account settings if desired).

## Storage and deployment limits

This local prototype uses one backend worker. Sessions, tokens, profiles, alerts, and trips for connected browsers expire after 24 hours or disappear on restart. Reconnect afterward. Before using multiple workers or durable production accounts, move these into an authenticated persistent store with encrypted tokens. Public GIS data remains shared; connected users' private data is isolated.

For deployment, update the Google redirect, frontend URL, and CORS origins to the deployed HTTPS URLs. Use `CALENDAR_COOKIE_SECURE=true`; if the frontend and API are on different sites, also use `CALENDAR_COOKIE_SAMESITE=none` (browser third-party-cookie restrictions can still block such setups). Prefer same-site hosting.

References: [Google server-side OAuth](https://developers.google.com/identity/protocols/oauth2/web-server), [Calendar events.list](https://developers.google.com/workspace/calendar/api/v3/reference/events/list).
