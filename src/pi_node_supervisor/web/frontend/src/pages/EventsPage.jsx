import {
  Empty,
  PageHeading,
  Pagination,
  Status,
} from "../components/Common.jsx";
import { useSupervisor } from "../SupervisorContext.jsx";
import { fmt } from "../utils.js";

const LABELS = {
  "configuration.capture_schedule_updated": "촬영 설정 변경",
  "configuration.power_settings_updated": "전원 설정 변경",
  "configuration.revision_applied": "고급 설정 변경",
  "power.schedule_disabled": "자동 전원 계획 비활성화",
  "power.schedule_enabled": "자동 전원 계획 활성화",
  "node.config.update_failed": "촬영 설정 적용 실패",
  "node.config.rollback_failed": "촬영 설정 복구 실패",
};

export default function EventsPage() {
  const { events, loadEvents, notify } = useSupervisor();
  const change = (offset, limit) =>
    loadEvents(offset, limit).catch((error) => notify(error.message, true));
  return (
    <section className="view active">
      <PageHeading
        eyebrow="AUDIT LOG"
        title="이벤트"
        description="카메라 재시작, 설정 변경, 오류처럼 장치에서 일어난 일을 시간순으로 보여줍니다."
      />
      <article className="panel events-panel">
        {events.items.length ? (
          <table className="events-table">
            <thead>
              <tr>
                <th>시각</th>
                <th>노드</th>
                <th>등급</th>
                <th>유형</th>
                <th>내용</th>
              </tr>
            </thead>
            <tbody>
              {events.items.map((event) => (
                <tr
                  key={
                    event.event_id || `${event.occurred_at}-${event.event_type}`
                  }
                >
                  <td>{fmt(event.occurred_at)}</td>
                  <td>{event.node_id || "—"}</td>
                  <td>
                    <Status value={event.severity} />
                  </td>
                  <td>
                    <strong>
                      {LABELS[event.event_type] || event.event_type}
                    </strong>
                  </td>
                  <td>
                    <code className="event-payload">
                      {JSON.stringify(event.payload)}
                    </code>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <Empty>표시할 이벤트가 없습니다.</Empty>
        )}
        <Pagination page={events} onChange={change} />
      </article>
    </section>
  );
}
