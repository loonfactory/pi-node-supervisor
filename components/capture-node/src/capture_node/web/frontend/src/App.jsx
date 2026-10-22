import EventsPanel from "./components/EventsPanel";
import PreviewPanel from "./components/PreviewPanel";
import SettingsPanel from "./components/SettingsPanel";
import StorageModal from "./components/StorageModal";
import TopBar from "./components/TopBar";
import { useCaptureNode } from "./hooks/useCaptureNode";

export default function App() {
  const model = useCaptureNode();

  return (
    <div className="app-shell">
      <TopBar status={model.status} />
      <main>
        <section className="workspace">
          <PreviewPanel model={model.preview} />
          <SettingsPanel model={model.settings} />
        </section>
        <EventsPanel model={model.events} />
      </main>
      <StorageModal model={model.storage} />
    </div>
  );
}
