import { useState } from "react";

import { captureNodeApi } from "../api/captureNodeApi";

export function useStorageBrowser(reportError) {
  const [open, setOpen] = useState(false);
  const [targets, setTargets] = useState([]);
  const [target, setTarget] = useState("");
  const [files, setFiles] = useState([]);
  const [page, setPage] = useState(1);
  const [meta, setMeta] = useState({ total: 0, pages: 1 });

  const loadFiles = async (selectedTarget, selectedPage) => {
    const body = await captureNodeApi.storageFiles(selectedTarget, selectedPage);
    setFiles(body.items);
    setMeta({ total: body.total, pages: body.pages });
  };

  const openBrowser = async () => {
    reportError("");
    try {
      const body = await captureNodeApi.storageTargets();
      if (body.items.length === 0) {
        throw new Error("No storage target is configured");
      }
      setTargets(body.items);
      const selectedTarget = body.items[0].name;
      setTarget(selectedTarget);
      setPage(1);
      setOpen(true);
      await loadFiles(selectedTarget, 1);
    } catch (cause) {
      reportError(cause.message);
    }
  };

  const changePage = async (selectedPage) => {
    setPage(selectedPage);
    try {
      await loadFiles(target, selectedPage);
    } catch (cause) {
      reportError(cause.message);
    }
  };

  const selectTarget = async (selectedTarget) => {
    setTarget(selectedTarget);
    setPage(1);
    try {
      await loadFiles(selectedTarget, 1);
    } catch (cause) {
      reportError(cause.message);
    }
  };

  return {
    openBrowser,
    model: {
      open,
      close: () => setOpen(false),
      targets,
      target,
      selectTarget,
      files,
      page,
      meta,
      changePage,
    },
  };
}
