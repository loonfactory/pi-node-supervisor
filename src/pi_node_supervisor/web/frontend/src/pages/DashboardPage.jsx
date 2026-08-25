import {
  Empty,
  MetricGrid,
  PageHeading,
  Status,
} from "../components/Common.jsx";
import RunTable from "../components/RunTable.jsx";
import { useSupervisor } from "../SupervisorContext.jsx";
import { fmt, splitInterval } from "../utils.js";

function duration(seconds) {
  const parts = splitInterval(seconds);
  return (
    [
      [parts.days, "일"],
      [parts.hours, "시간"],
      [parts.minutes, "분"],
      [parts.seconds, "초"],
    ]
      .filter(([value]) => Number(value) > 0)
      .map(([value, unit]) => `${value}${unit}`)
      .join(" ") || "0초"
  );
}
function clock(value) {
  const [hour, minute, second = "00"] = String(value).split(":");
  const number = Number(hour);
  return `${number < 12 ? "오전" : "오후"} ${String(number % 12 || 12).padStart(2, "0")}:${minute}:${second}`;
}

function Timeline({ configuration, plan }) {
  const policies = configuration?.policies || {};
  const cards = [];
  for (const schedule of configuration?.capture_schedules || [])
    if (schedule.mode !== "disabled")
      cards.push({
        key: `capture-${schedule.id}`,
        title: "사진 촬영",
        lines:
          schedule.mode === "interval"
            ? [`${duration(schedule.interval_seconds)} 간격으로 계속 촬영`]
            : (schedule.daily_times || []).map(
                (time) => `${clock(time)}에 촬영`,
              ),
        tags: [
          schedule.node,
          `작동 전 ${duration(policies["boot-lead-seconds"] || 0)}`,
          `작동 후 ${duration(policies["shutdown-grace-seconds"] || 0)}`,
        ],
      });
  for (const schedule of configuration?.sensor_schedules || [])
    if (schedule.mode !== "disabled")
      cards.push({
        key: `sensor-${schedule.id}`,
        title: "토양 센서 수집",
        lines: (schedule.daily_times || []).map(
          (time) => `${clock(time)}에 수집`,
        ),
        tags: [schedule.node],
      });
  for (const schedule of configuration?.power_keep_schedules || [])
    cards.push({
      key: `keep-${schedule.id}`,
      title: "전원 유지",
      lines: [`${clock(schedule.start)}부터 ${clock(schedule.end)}까지`],
      tags: ["매일 반복"],
    });
  const recurring = new Set(
    [
      ...(configuration?.capture_schedules || []),
      ...(configuration?.sensor_schedules || []),
      ...(configuration?.power_keep_schedules || []),
    ].map((item) => item.id),
  );
  for (const window of plan?.power_windows || [])
    if (
      window.activity_ids.some(
        (id) =>
          ![...recurring].some(
            (base) => id === base || id.startsWith(`${base}-`),
          ),
      )
    )
      cards.push({
        key: `window-${window.power_on}`,
        title: `${fmt(window.power_on)} → ${fmt(window.power_down)}`,
        lines: [
          window.automatic_shutdown_possible
            ? `RTC 다음 기동 ${fmt(window.next_power_on)}`
            : "다음 기동 없음 · 자동 종료 보류",
        ],
        tags: window.activity_ids,
      });
  return cards.length ? (
    cards.map((card) => (
      <div className="window" key={card.key}>
        <strong>{card.title}</strong>
        {card.lines.length ? (
          card.lines.map((line, index) => (
            <small className="window-detail" key={index}>
              {line}
            </small>
          ))
        ) : (
          <small className="window-detail">등록된 시각 없음</small>
        )}
        <div className="activity-tags">
          {card.tags.map((tag) => (
            <span className="tag" key={tag}>
              {tag}
            </span>
          ))}
        </div>
      </div>
    ))
  ) : (
    <Empty>등록된 반복 일정이 없습니다.</Empty>
  );
}

export default function DashboardPage() {
  const { dashboard, runs, configuration, refresh, notify, setView } =
    useSupervisor();
  const runtime = dashboard?.runtime || {};
  const active = dashboard?.revision?.active_revision;
  const cameraNodes = (dashboard?.nodes || []).filter((node) =>
    node.node_id?.startsWith("camera"),
  );
  const healthy = cameraNodes.filter((node) => node.healthy).length;
  const metrics = [
    [
      "적용 중인 설정",
      active ? `#${active.id}` : "없음",
      active ? "good" : "alert",
    ],
    [
      "연결된 카메라",
      `${healthy}/${cameraNodes.length}`,
      healthy === cameraNodes.length ? "good" : "alert",
    ],
    ["수집 데이터", runtime.data || 0, ""],
    [
      "촬영 오류",
      runtime.failed_runs || 0,
      runtime.failed_runs ? "alert" : "good",
    ],
  ];
  return (
    <section className="view active">
      <PageHeading
        eyebrow="SYSTEM OVERVIEW"
        title="장치 운영 현황"
        actions={
          <button
            onClick={() =>
              refresh().then(() => notify("노드 상태를 갱신했습니다."))
            }
          >
            노드 상태 갱신
          </button>
        }
      />
      <MetricGrid items={metrics} />
      <div className="two-column">
        <article className="panel">
          <div className="panel-title">
            <h2>전원 타임라인</h2>
            <span>Active revision</span>
          </div>
          <div className="timeline">
            <Timeline
              configuration={configuration}
              plan={dashboard?.active_plan}
            />
          </div>
        </article>
        <article className="panel">
          <div className="panel-title">
            <h2>노드 상태</h2>
            <span>Live</span>
          </div>
          <div className="stack">
            {dashboard?.nodes?.length ? (
              dashboard.nodes.map((node) => (
                <div className="node-row" key={node.node_id}>
                  <div>
                    <strong>{node.node_id}</strong>
                    <small>
                      {node.camera_detail || node.error || "카메라 API"}
                    </small>
                  </div>
                  <Status value={node.healthy ? "healthy" : "unhealthy"} />
                </div>
              ))
            ) : (
              <Empty>실행 가능한 노드가 없습니다.</Empty>
            )}
          </div>
        </article>
      </div>
      <article className="panel">
        <div className="panel-title">
          <h2>최근 촬영</h2>
          <button className="text-button" onClick={() => setView("data")}>
            저장 데이터 보기
          </button>
        </div>
        <RunTable items={runs.slice(0, 5)} />
      </article>
    </section>
  );
}
