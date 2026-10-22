import { Camera } from "lucide-react";

export default function TopBar({ status }) {
  return (
    <header className="topbar">
      <div className="brand">
        <Camera size={22} aria-hidden="true" />
        <strong>CaptureNode</strong>
      </div>
      <div className="runtime-state">
        <span
          className={`status-dot ${
            status.cameraOnline ? "online" : status.serverOnline ? "error" : ""
          }`}
        />
        {status.label}
      </div>
    </header>
  );
}
