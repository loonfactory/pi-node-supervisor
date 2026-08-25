import { useSupervisor } from "../SupervisorContext.jsx";

const VIEWS = [
  ["dashboard", "대시보드"],
  ["cameras", "카메라"],
  ["sensors", "토양 센서"],
  ["settings", "설정"],
  ["events", "이벤트"],
  ["data", "데이터"],
  ["nodes", "노드"],
];

export function TopBar() {
  const { connection, refresh, setTokenOpen } = useSupervisor();
  const labels = {
    checking: "연결 확인 중",
    connected: "장치 연결됨",
    failed: "연결 실패",
  };
  const classes = { checking: "muted", connected: "ok", failed: "bad" };
  return (
    <header className="topbar">
      <div className="brand">
        <span className="brand-mark">π</span>
        <div>
          <strong>Node Supervisor</strong>
          <small>RASPBERRY PI · LOCAL CONTROL</small>
        </div>
      </div>
      <div className="top-actions">
        <span className={`pill ${classes[connection]}`}>
          {labels[connection]}
        </span>
        <button className="ghost" onClick={() => setTokenOpen(true)}>
          접속 토큰
        </button>
        <button onClick={refresh}>갱신</button>
      </div>
    </header>
  );
}

export function Sidebar() {
  const { view, setView } = useSupervisor();
  return (
    <nav className="sidebar" aria-label="주 메뉴">
      {VIEWS.map(([id, label]) => (
        <button
          key={id}
          className={`nav-item${view === id ? " active" : ""}`}
          onClick={() => setView(id)}
        >
          {label}
        </button>
      ))}
    </nav>
  );
}
