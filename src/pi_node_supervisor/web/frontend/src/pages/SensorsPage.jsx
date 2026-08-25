import { useState } from "react";

import { api } from "../api-client.js";
import {
  Empty,
  MetricGrid,
  PageHeading,
  Status,
} from "../components/Common.jsx";
import { useSupervisor } from "../SupervisorContext.jsx";
import { fmt } from "../utils.js";

export default function SensorsPage() {
  const { sensors, nodes, loadSensors, notify } = useSupervisor();
  const [syncing, setSyncing] = useState(false);
  const latest = sensors.data[0];
  const counts = sensors.delivery.counts || {};
  const sensorNodes = nodes.filter((node) => node.kind === "sensor-node");
  const metrics = [
    [
      "등록 토양 센서",
      sensorNodes.length,
      sensorNodes.length ? "good" : "alert",
    ],
    [
      "최근 온도",
      latest ? `${latest.temperature_c.toLocaleString()} °C` : "—",
      latest ? "good" : "",
    ],
    [
      "최근 습도",
      latest ? `${latest.humidity_percent.toLocaleString()} %` : "—",
      latest ? "good" : "",
    ],
    [
      "원격 재시도",
      (counts.pending || 0) + (counts.failed || 0),
      counts.failed ? "alert" : "good",
    ],
  ];
  const sync = async () => {
    setSyncing(true);
    try {
      await api("/api/sensor-storage/sync", { method: "POST" });
      await loadSensors();
      notify("전송 가능한 CSV의 원격 복제를 다시 시도했습니다.");
    } catch (error) {
      notify(error.message, true);
    } finally {
      setSyncing(false);
    }
  };
  return (
    <section className="view active">
      <PageHeading
        eyebrow="TEMPERATURE · HUMIDITY"
        title="토양 센서 데이터"
        description="로컬 CSV 원본과 file/SFTP/FTPS 복제 상태를 함께 확인합니다."
        actions={
          <div className="button-row">
            <button className="ghost" disabled={syncing} onClick={sync}>
              원격 재시도
            </button>
            <button
              onClick={() =>
                loadSensors()
                  .then(() => notify("토양 센서 상태를 갱신했습니다."))
                  .catch((error) => notify(error.message, true))
              }
            >
              갱신
            </button>
          </div>
        }
      />
      <MetricGrid items={metrics} />
      <article className="panel sensor-data-panel">
        <div className="panel-title">
          <div>
            <h2>온·습도 측정값</h2>
            <span>CSV의 timestamp, temperature_c, humidity_percent</span>
          </div>
        </div>
        {sensors.error ? (
          <Empty error>{sensors.error}</Empty>
        ) : sensors.data.length ? (
          <table className="sensor-data-table">
            <thead>
              <tr>
                <th>예약 시각</th>
                <th>토양 센서</th>
                <th>온도 °C</th>
                <th>습도 %</th>
                <th>저장 상태</th>
              </tr>
            </thead>
            <tbody>
              {sensors.data.map((item, index) => (
                <tr key={`${item.node_id}-${item.timestamp}-${index}`}>
                  <td>{fmt(item.timestamp)}</td>
                  <td>
                    <strong>{item.node_id}</strong>
                  </td>
                  <td>{item.temperature_c}</td>
                  <td>{item.humidity_percent}</td>
                  <td>
                    <Status value={item.sync_status} />
                    <br />
                    <code>{item.relative_path}</code>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <Empty>로컬 CSV에 저장된 온·습도 측정값이 없습니다.</Empty>
        )}
      </article>
      <article className="panel sensor-delivery-panel">
        <div className="panel-title">
          <div>
            <h2>원격 동기화 상태</h2>
            <span>로컬 CSV 전체 파일의 원자적 복제 결과</span>
          </div>
        </div>
        {sensors.delivery.items?.length ? (
          <table className="sensor-delivery-table">
            <thead>
              <tr>
                <th>갱신 시각</th>
                <th>토양 센서 / 대상</th>
                <th>상태</th>
                <th>시도</th>
                <th>다음 재시도</th>
                <th>CSV / 오류</th>
              </tr>
            </thead>
            <tbody>
              {sensors.delivery.items.map((item, index) => (
                <tr
                  key={`${item.node_id}-${item.target}-${item.relative_path}-${index}`}
                >
                  <td>{fmt(item.updated_at)}</td>
                  <td>
                    <strong>{item.node_id}</strong>
                    <br />
                    <small>{item.target}</small>
                  </td>
                  <td>
                    <Status value={item.status} />
                  </td>
                  <td>{item.attempts}</td>
                  <td>{fmt(item.next_attempt_at)}</td>
                  <td>
                    <code>{item.relative_path}</code>
                    {item.last_error && (
                      <>
                        <br />
                        <small className="danger-text">{item.last_error}</small>
                      </>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <Empty>
            설정된 원격 복제 이력이 없습니다. 로컬 CSV 수집은 독립적으로
            계속됩니다.
          </Empty>
        )}
      </article>
    </section>
  );
}
