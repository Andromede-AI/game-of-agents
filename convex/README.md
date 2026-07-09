# Convex Backend

This folder contains the monitoring backend for synced run state, snapshots, and events.

The Python service is expected to POST to:

- `/sync/run`
- `/sync/event`
- `/sync/batch`

To finish setup, run Convex codegen and deploy from the repo root from the repo root.
