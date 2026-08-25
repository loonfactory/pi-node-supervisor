import { useEffect, useRef, useState } from "react";

import { useSupervisor } from "../SupervisorContext.jsx";

const STATUS_LABELS = {
  healthy: "정상",
  unhealthy: "확인 필요",
  running: "실행 중",
  succeeded: "완료",
  failed: "실패",
  interrupted: "중단됨",
  missed: "놓침",
  info: "안내",
  warning: "주의",
  error: "오류",
  pending: "대기",
  not_queued: "미전송",
  synchronized: "동기화",
  "local-only": "로컬만",
};

export function Status({ value }) {
  return (
    <span className={`status ${value}`}>
      {STATUS_LABELS[value] || value || "—"}
    </span>
  );
}

export function Empty({ children = "표시할 항목이 없습니다.", error = false }) {
  return (
    <div className={error ? "friendly-empty error-copy" : "empty"}>
      {children}
    </div>
  );
}

export function PageHeading({ eyebrow, title, description, actions }) {
  return (
    <div className="page-heading">
      <div>
        <p className="eyebrow">{eyebrow}</p>
        <h1>{title}</h1>
        {description && <p className="page-description">{description}</p>}
      </div>
      {actions}
    </div>
  );
}

export function MetricGrid({ items }) {
  return (
    <div className="metric-grid">
      {items.map(([label, value, cls = ""]) => (
        <article className="metric" key={label}>
          <span>{label}</span>
          <strong className={cls}>{value}</strong>
        </article>
      ))}
    </div>
  );
}

export function Pagination({ page, onChange }) {
  const first = page.total ? page.offset + 1 : 0;
  const last = Math.min(page.offset + page.items.length, page.total);
  return (
    <div className="pagination">
      <label>
        페이지당{" "}
        <select
          value={page.limit}
          onChange={(event) => onChange(0, Number(event.target.value))}
        >
          <option value="25">25건</option>
          <option value="50">50건</option>
          <option value="100">100건</option>
        </select>
      </label>
      <span>
        전체 {page.total.toLocaleString()}건 · {first.toLocaleString()}–
        {last.toLocaleString()}건
      </span>
      <div className="pagination-actions">
        <button
          type="button"
          className="ghost"
          disabled={!page.offset}
          onClick={() =>
            onChange(Math.max(0, page.offset - page.limit), page.limit)
          }
        >
          이전
        </button>
        <button
          type="button"
          className="ghost"
          disabled={page.offset + page.items.length >= page.total}
          onClick={() => onChange(page.offset + page.limit, page.limit)}
        >
          다음
        </button>
      </div>
    </div>
  );
}

export function TokenDialog() {
  const { tokenOpen, setTokenOpen, saveToken } = useSupervisor();
  const [value, setValue] = useState("");
  const ref = useRef(null);
  useEffect(() => {
    if (tokenOpen && !ref.current.open) ref.current.showModal();
    if (!tokenOpen && ref.current.open) ref.current.close();
  }, [tokenOpen]);
  return (
    <dialog ref={ref} onClose={() => setTokenOpen(false)}>
      <form
        method="dialog"
        onSubmit={(event) => {
          event.preventDefault();
          saveToken(value);
        }}
      >
        <p className="eyebrow">LOCAL AUTHENTICATION</p>
        <h2>접속 토큰</h2>
        <p className="help">
          토큰은 이 브라우저의 sessionStorage에만 보관됩니다.
        </p>
        <input
          type="password"
          autoComplete="current-password"
          placeholder="X-Supervisor-Token"
          value={value}
          onChange={(event) => setValue(event.target.value)}
        />
        <div className="dialog-actions">
          <button
            type="button"
            className="ghost"
            onClick={() => setTokenOpen(false)}
          >
            취소
          </button>
          <button type="submit">연결</button>
        </div>
      </form>
    </dialog>
  );
}

export function Toast() {
  const { toast } = useSupervisor();
  const [visible, setVisible] = useState(false);
  useEffect(() => {
    if (!toast) return undefined;
    setVisible(true);
    const timer = setTimeout(() => setVisible(false), 3200);
    return () => clearTimeout(timer);
  }, [toast]);
  return (
    <div
      id="toast"
      className={`${visible ? "show" : ""}${toast?.error ? " error" : ""}`}
      role="status"
      aria-live="polite"
    >
      {toast?.message}
    </div>
  );
}
