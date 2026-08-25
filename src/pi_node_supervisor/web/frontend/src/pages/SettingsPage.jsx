import { PageHeading } from "../components/Common.jsx";
import AdvancedConfigEditor from "../components/settings/AdvancedConfigEditor.jsx";
import CameraStorageSettings from "../components/settings/CameraStorageSettings.jsx";
import CaptureScheduleSettings from "../components/settings/CaptureScheduleSettings.jsx";
import PowerSettings from "../components/settings/PowerSettings.jsx";
import SensorScheduleSettings from "../components/settings/SensorScheduleSettings.jsx";
import SensorSettings from "../components/settings/SensorSettings.jsx";

export default function SettingsPage() {
  return (
    <section className="view active">
      <PageHeading
        eyebrow="DEVICE SETTINGS"
        title="설정"
        description="카메라 촬영·토양 센서 수집 시각과 저장 위치를 한곳에서 관리합니다."
      />
      <div className="settings-grid">
        <CaptureScheduleSettings />
        <SensorSettings />
        <SensorScheduleSettings />
        <PowerSettings />
        <CameraStorageSettings />
        <AdvancedConfigEditor />
      </div>
    </section>
  );
}
