import { Empty, MetricGrid, PageHeading, Status } from "../components/Common.jsx";
import RunTable from "../components/RunTable.jsx";
import { useSupervisor } from "../SupervisorContext.jsx";

function ScheduleSummary({ configuration }) {
  const schedules = Object.entries(configuration?.schedules || {});
  const windows = configuration?.power?.windows || [];
  if (!schedules.length && !windows.length) return <Empty>등록된 일정이 없습니다.</Empty>;
  return <div className="stack">
    {schedules.map(([name, entries]) => <div className="window" key={name}>
      <strong>{name}</strong>
      {(entries || []).map((entry, index) => <small className="window-detail" key={index}>
        {entry.type === "interval" ? `${entry.startTime || "00:00:00"}–${entry.endTime || "24:00:00"} · ${entry.intervalSeconds}초 간격` : `매일 ${entry.time}`}
      </small>)}
    </div>)}
    {windows.map((window, index) => <div className="window" key={`power-${index}`}>
      <strong>전원 유지</strong><small className="window-detail">{window.startTime}–{window.endTime}</small>
    </div>)}
  </div>;
}

export default function DashboardPage() {
  const { dashboard, runs, configuration, refresh, notify, setView } = useSupervisor();
  const nodes = dashboard?.nodes || [];
  const activeNodes = nodes.filter((node) => node.active).length;
  const metrics = [
    ["관리 장치", `${activeNodes}/${nodes.length}`, activeNodes === nodes.length ? "good" : "alert"],
    ["실행 이력", dashboard?.runs || 0, ""],
    ["산출물", dashboard?.artifacts || 0, ""],
    ["오류·중단", dashboard?.failedRuns || 0, dashboard?.failedRuns ? "alert" : "good"],
  ];
  return <section className="view active">
    <PageHeading eyebrow="SYSTEM OVERVIEW" title="장치 운영 현황" actions={<button onClick={() => refresh().then(() => notify("상태를 갱신했습니다."))}>상태 갱신</button>} />
    <MetricGrid items={metrics} />
    <div className="two-column">
      <article className="panel"><div className="panel-title"><h2>일정</h2><span>Active revision</span></div><ScheduleSummary configuration={configuration} /></article>
      <article className="panel"><div className="panel-title"><h2>장치 상태</h2><span>Live</span></div><div className="stack">
        {nodes.length ? nodes.map((node) => <div className="node-row" key={node.id}><div><strong>{node.id}</strong><small>{node.kind}</small></div><Status value={node.active ? "healthy" : "unhealthy"} /></div>) : <Empty>관리 중인 장치가 없습니다.</Empty>}
      </div></article>
    </div>
    <article className="panel"><div className="panel-title"><h2>최근 실행</h2><button className="text-button" onClick={() => setView("data")}>저장 데이터 보기</button></div><RunTable items={runs.slice(0, 5)} /></article>
  </section>;
}
