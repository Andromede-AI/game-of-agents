"use client";

import { ConvexProvider, ConvexReactClient } from "convex/react";
import { useState } from "react";

export function Providers({ children }: { children: React.ReactNode }) {
  const [client] = useState(() => {
    const url = process.env.NEXT_PUBLIC_CONVEX_URL;
    return url ? new ConvexReactClient(url) : null;
  });

  if (!client) {
    return children;
  }

  return <ConvexProvider client={client}>{children}</ConvexProvider>;
}
