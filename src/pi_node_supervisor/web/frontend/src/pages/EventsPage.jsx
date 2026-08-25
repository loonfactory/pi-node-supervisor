import {
  Empty,
  PageHeading,
  Pagination,
  Status,
} from "../components/Common.jsx";
import { useSupervisor } from "../SupervisorContext.jsx";
import { fmt } from "../utils.js";

const LABELS = {
  "run.started": "작업 시작",
  "run.succeeded": "작업 완료",
  "run.failed": "작업 실패",
  "runtime.interrupted": "재시작으로 중단",
  "storage.failed": "저장 실패",
  "storage.temporary_cleanup_failed": "임시 파일 정리 실패",
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
                    event.id || `${event.occurredAt}-${event.eventType}`
                  }
                >
                  <td>{fmt(event.occurredAt)}</td>
                  <td>{event.deviceId || "—"}</td>
                  <td>
                    <Status value={event.severity} />
                  </td>
                  <td>
                    <strong>
                      {LABELS[event.eventType] || event.eventType}
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
