import { useEffect, useState } from "react";

// A ticking clock for the one component that needs it, at the cadence it needs, so the 1-second
// header clock never re-renders the dashboard or the chart.
export default function useNow(intervalMs) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(id);
  }, [intervalMs]);
  return now;
}
