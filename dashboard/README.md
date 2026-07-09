# Dashboard

Minimal Next.js monitoring UI for Convex-backed tournament runs.

## Expected Convex query contract

The app calls these public Convex query names directly:

- `monitor:listRuns`
- `monitor:getRunDashboard`
- `monitor:listRecentEvents`

## Local usage

```bash
cd dashboard
npm install
NEXT_PUBLIC_CONVEX_URL=... API_URL=... API_TOKEN=... npm run dev
```

## Vercel

Deploy the `dashboard/` directory as the Vercel project root and set:

- `NEXT_PUBLIC_CONVEX_URL`
- `API_URL`
- `API_TOKEN`
