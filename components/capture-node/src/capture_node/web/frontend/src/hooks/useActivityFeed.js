import { useEffect, useRef, useState } from "react";

import { captureNodeApi } from "../api/captureNodeApi";

export function useActivityFeed(onCaptureChanged) {
  const [items, setItems] = useState([]);
  const [page, setPage] = useState(1);
  const [meta, setMeta] = useState({ total: 0, pages: 1 });
  const lastCaptureIdRef = useRef(null);
  const loadedRef = useRef(false);
  const onCaptureChangedRef = useRef(onCaptureChanged);
  onCaptureChangedRef.current = onCaptureChanged;

  const load = async (requestedPage) => {
    const body = await captureNodeApi.events(requestedPage);
    if (!body) return;
    setItems(body.items);
    setMeta({ total: body.total, pages: body.pages });
    if (requestedPage > body.pages) setPage(body.pages);
    const latestCaptureId = body.latestCaptureId || null;
    const captureChanged =
      loadedRef.current &&
      latestCaptureId &&
      latestCaptureId !== lastCaptureIdRef.current;
    lastCaptureIdRef.current = latestCaptureId;
    loadedRef.current = true;
    if (captureChanged) await onCaptureChangedRef.current();
  };

  useEffect(() => {
    load(page);
    const timer = window.setInterval(() => load(page), 1500);
    return () => window.clearInterval(timer);
  }, [page]);

  const refreshAfterCapture = async (captureId) => {
    lastCaptureIdRef.current = captureId;
    setPage(1);
    await load(1);
  };

  return {
    model: {
      items,
      page,
      meta,
      previous: () => setPage((current) => Math.max(1, current - 1)),
      next: () => setPage((current) => Math.min(meta.pages, current + 1)),
    },
    refreshAfterCapture,
  };
}
