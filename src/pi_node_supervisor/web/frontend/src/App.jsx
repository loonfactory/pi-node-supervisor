import { Toast, TokenDialog } from "./components/Common.jsx";
import { Sidebar, TopBar } from "./components/Layout.jsx";
import CamerasPage from "./pages/CamerasPage.jsx";
import DashboardPage from "./pages/DashboardPage.jsx";
import DataPage from "./pages/DataPage.jsx";
import EventsPage from "./pages/EventsPage.jsx";
import NodesPage from "./pages/NodesPage.jsx";
import SensorsPage from "./pages/SensorsPage.jsx";
import SettingsPage from "./pages/SettingsPage.jsx";
import { SupervisorProvider, useSupervisor } from "./SupervisorContext.jsx";

const PAGES = {
  dashboard: DashboardPage,
  cameras: CamerasPage,
  sensors: SensorsPage,
  settings: SettingsPage,
  events: EventsPage,
  data: DataPage,
  nodes: NodesPage,
};

function Application() {
  const { view } = useSupervisor();
  const Page = PAGES[view] || DashboardPage;
  return (
    <>
      <TopBar />
      <div className="shell">
        <Sidebar />
        <main>
          <Page />
        </main>
      </div>
      <TokenDialog />
      <Toast />
    </>
  );
}

export default function App() {
  return (
    <SupervisorProvider>
      <Application />
    </SupervisorProvider>
  );
}
