import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";

import { api, token } from "./api-client.js";
import { friendlyError } from "./utils.js";

const SupervisorContext = createContext(null);

export function SupervisorProvider({ children }) {
  const [view, setView] = useState("dashboard");
  const [connection, setConnection] = useState("checking");
  const [tokenOpen, setTokenOpen] = useState(false);
  const [toast, setToast] = useState(null);
  const [dashboard, setDashboard] = useState(null);
  const [runs, setRuns] = useState([]);
  const [nodes, setNodes] = useState([]);
  const [configuration, setConfiguration] = useState(null);
  const [events, setEvents] = useState({
    items: [],
    total: 0,
    offset: 0,
    limit: 25,
  });
  const [data, setData] = useState({
    items: [],
    total: 0,
    offset: 0,
    limit: 25,
  });
  const [sensors, setSensors] = useState({
    data: [],
    delivery: { counts: {}, items: [] },
    settings: [],
    restartRequired: false,
    error: null,
  });

  const notify = useCallback((message, error = false) => {
    setToast({ message, error, key: Date.now() });
  }, []);

  const loadEvents = useCallback(
    async (offset = events.offset, limit = events.limit) => {
      const result = await api(`/api/events?limit=${limit}&offset=${offset}`);
      const total = result.total ?? result.items.length;
      const adjusted =
        total && offset >= total
          ? Math.floor((total - 1) / limit) * limit
          : offset;
      if (adjusted !== offset) return loadEvents(adjusted, limit);
      setEvents({ items: result.items, total, offset, limit });
    },
    [events.limit, events.offset],
  );

  const loadData = useCallback(
    async (offset = data.offset, limit = data.limit) => {
      const result = await api(`/api/data?limit=${limit}&offset=${offset}`);
      const total = result.total ?? result.items.length;
      const adjusted =
        total && offset >= total
          ? Math.floor((total - 1) / limit) * limit
          : offset;
      if (adjusted !== offset) return loadData(adjusted, limit);
      setData({ items: result.items, total, offset, limit });
    },
    [data.limit, data.offset],
  );

  const loadSensors = useCallback(async () => {
    try {
      const [measurements, delivery, settings] = await Promise.all([
        api("/api/sensor-data?limit=100"),
        api("/api/sensor-storage"),
        api("/api/sensor-settings"),
      ]);
      setSensors({
        data: measurements.items,
        delivery,
        settings: settings.items,
        restartRequired: settings.restart_required,
        error: null,
      });
    } catch (error) {
      setSensors((current) => ({ ...current, error: friendlyError(error) }));
      throw error;
    }
  }, []);

  const refresh = useCallback(async () => {
    try {
      await api("/api/health");
      setConnection("connected");
      if (!token()) {
        setTokenOpen(true);
        return;
      }
      const [
        nextDashboard,
        nextRuns,
        nextEvents,
        nextData,
        nextNodes,
        nextConfiguration,
      ] = await Promise.all([
        api("/api/dashboard"),
        api("/api/runs?limit=100"),
        api(`/api/events?limit=${events.limit}&offset=${events.offset}`),
        api(`/api/data?limit=${data.limit}&offset=${data.offset}`),
        api("/api/nodes"),
        api("/api/config/active"),
      ]);
      setDashboard(nextDashboard);
      setRuns(nextRuns.items);
      setNodes(nextNodes.items);
      setConfiguration(nextConfiguration);
      setEvents((current) => ({
        ...current,
        items: nextEvents.items,
        total: nextEvents.total ?? nextEvents.items.length,
      }));
      setData((current) => ({
        ...current,
        items: nextData.items,
        total: nextData.total ?? nextData.items.length,
      }));
      loadSensors().catch(() => {});
    } catch (error) {
      setConnection("failed");
      notify(friendlyError(error), true);
    }
  }, [
    data.limit,
    data.offset,
    events.limit,
    events.offset,
    loadSensors,
    notify,
  ]);

  useEffect(() => {
    const unauthorized = () => setTokenOpen(true);
    window.addEventListener("supervisor:unauthorized", unauthorized);
    refresh();
    return () =>
      window.removeEventListener("supervisor:unauthorized", unauthorized);
  }, []); // initial device discovery only

  const saveToken = useCallback(
    (value) => {
      sessionStorage.setItem("supervisor-token", value);
      setTokenOpen(false);
      refresh();
    },
    [refresh],
  );

  const value = useMemo(
    () => ({
      view,
      setView,
      connection,
      tokenOpen,
      setTokenOpen,
      toast,
      notify,
      dashboard,
      runs,
      nodes,
      configuration,
      events,
      data,
      sensors,
      refresh,
      loadEvents,
      loadData,
      loadSensors,
      saveToken,
    }),
    [
      view,
      connection,
      tokenOpen,
      toast,
      notify,
      dashboard,
      runs,
      nodes,
      configuration,
      events,
      data,
      sensors,
      refresh,
      loadEvents,
      loadData,
      loadSensors,
      saveToken,
    ],
  );
  return (
    <SupervisorContext.Provider value={value}>
      {children}
    </SupervisorContext.Provider>
  );
}

export function useSupervisor() {
  const value = useContext(SupervisorContext);
  if (!value)
    throw new Error("useSupervisor must be used inside SupervisorProvider");
  return value;
}
