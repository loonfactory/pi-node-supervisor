import { useState } from "react";

import { api } from "../api-client.js";
import { Empty, MetricGrid, PageHeading, Status } from "../components/Common.jsx";
import { useSupervisor } from "../SupervisorContext.jsx";
import { fmt, friendlyError } from "../utils.js";

export default function SensorsPage() {
  const { runs, nodes, refresh, notify } = useSupervisor();
  const [collecting, setCollecting] = useState(false);
  const sensors = nodes.filter((node) => node.kind === "sensor");
  const measurements = runs.filter((run) => run.deviceKind === "sensor" && run.result?.observedAt);
  const latest = measurements[0];
  const metrics = [
    ["등록 센서", sensors.length, sensors.length ? "good" : "alert"],
    ["최근 온도", latest ? `${latest.result.temperatureC} °C` : "—", latest ? "good" : ""],
    ["최근 습도", latest ? `${latest.result.humidityPercent} %` : "—", latest ? "good" : ""],
    ["수집 이력", measurements.length, ""],
  ];
  const collect = async () => {
    setCollecting(true);
    try {
      const result = await api("/api/sensors/collect", { method: "POST" });
      await refresh();
      const failed = result.items.filter((item) => item.status !== "succeeded");
      notify(
        failed.length
          ? `${result.items.length - failed.length}개 센서 수집 완료, ${failed.length}개 실패`
          : `${result.items.length}개 센서의 최신 값을 수집하고 저장했습니다.`,
        Boolean(failed.length),
      );
    } catch (error) {
      notify(friendlyError(error), true);
    } finally {
      setCollecting(false);
    }
  };
  return <section className="view active">
    <PageHeading eyebrow="TEMPERATURE · HUMIDITY" title="센서 데이터" description="센서 취득 결과와 각 저장 대상의 완료 여부를 실행 이력에서 확인합니다. 실패한 저장은 자동 재시도하지 않습니다." actions={<button disabled={collecting || !sensors.length} onClick={collect}>{collecting ? "수집 및 저장 중…" : "최신 값 수집"}</button>} />
    <MetricGrid items={metrics} />
    <article className="panel sensor-data-panel">
      {measurements.length ? <table className="sensor-data-table"><thead><tr><th>취득 시각</th><th>센서</th><th>온도 °C</th><th>습도 %</th><th>저장</th></tr></thead><tbody>
        {measurements.map((run) => <tr key={run.runId}><td>{fmt(run.result.observedAt)}</td><td><strong>{run.deviceId}</strong></td><td>{run.result.temperatureC}</td><td>{run.result.humidityPercent}</td><td><Status value={run.status} /> {run.result.storageSucceeded}/{run.result.storageAttempted}</td></tr>)}
      </tbody></table> : <Empty>표시할 센서 측정값이 없습니다.</Empty>}
    </article>
  </section>;
}
