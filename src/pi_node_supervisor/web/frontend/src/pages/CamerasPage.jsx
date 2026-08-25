import { useState } from "react";

import { api } from "../api-client.js";
import { Empty, PageHeading, Status } from "../components/Common.jsx";
import { useCameraStream } from "../hooks/useCameraStream.js";
import { useSupervisor } from "../SupervisorContext.jsx";
import { friendlyError } from "../utils.js";

function CameraCard({ node, generation }) {
  const stream = useCameraStream(node.id, true);
  void generation;
  return (
    <article className="camera-card">
      <div className="panel-title">
        <div>
          <h2>{node.id}</h2>
          <span>{node.endpoint || "loopback"}</span>
        </div>
        <Status value={node.active ? "healthy" : "unhealthy"} />
      </div>
      <div className="camera-frame">
        {stream.src ? (
          <img src={stream.src} alt={`${node.id} 카메라 프리뷰`} />
        ) : (
          <div className="camera-placeholder">
            {stream.error || "프레임을 불러오는 중입니다."}
          </div>
        )}
      </div>
      <div className="camera-footer">
        <span>{stream.status}</span>
        <button className="ghost" onClick={stream.reconnect}>
          스트림 재연결
        </button>
      </div>
    </article>
  );
}

export default function CamerasPage() {
  const { nodes, notify } = useSupervisor();
  const [busy, setBusy] = useState(false);
  const [generation, setGeneration] = useState(0);
  const cameras = nodes.filter((node) => node.kind === "camera");
  const captureAll = async () => {
    setBusy(true);
    try {
      const result = await api("/api/captures/manual", { method: "POST" });
      const failed = result.items.filter((item) => item.status !== "succeeded");
      notify(
        failed.length
          ? `${result.items.length - failed.length}대 촬영 완료, ${failed.length}대 실패`
          : `모든 카메라 ${result.items.length}대의 촬영과 저장이 완료되었습니다.`,
        Boolean(failed.length),
      );
    } catch (error) {
      notify(friendlyError(error), true);
    } finally {
      setBusy(false);
    }
  };
  return (
    <section className="view active">
      <PageHeading
        eyebrow="LIVE CAMERA CHECK"
        title="카메라 프리뷰"
        actions={
          <div className="button-row">
            <button disabled={busy} onClick={captureAll}>
              {busy ? "촬영 및 저장 중…" : "모든 카메라 즉시 촬영"}
            </button>
            <button
              className="ghost"
              onClick={() => setGeneration((value) => value + 1)}
            >
              스트림 재연결
            </button>
          </div>
        }
      />
      <p className="camera-help">
        카메라 상태를 실시간으로 확인합니다. 즉시 촬영한 사진은 현재 설정된
        카메라 저장소에 저장됩니다.
      </p>
      <div className="camera-grid">
        {cameras.length ? (
          cameras.map((node) => (
            <CameraCard
              key={`${node.id}-${generation}`}
              node={node}
              generation={generation}
            />
          ))
        ) : (
          <Empty>등록된 카메라가 없습니다.</Empty>
        )}
      </div>
    </section>
  );
}
