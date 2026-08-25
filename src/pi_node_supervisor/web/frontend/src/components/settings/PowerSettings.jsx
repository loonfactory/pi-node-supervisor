import { useEffect, useState } from "react";

import { api } from "../../api-client.js";
import { useSupervisor } from "../../SupervisorContext.jsx";
import {
  captureTimeSeconds,
  DEFAULT_POWER_KEEP_SCHEDULES,
} from "../../utils.js";

export default function PowerSettings() {
  const { configuration, refresh, notify } = useSupervisor();
  const [warmup, setWarmup] = useState(120);
  const [cleanup, setCleanup] = useState(60);
  const [items, setItems] = useState(DEFAULT_POWER_KEEP_SCHEDULES);
  const [note, setNote] = useState(
    "전원 유지 일정은 촬영 일정과 독립적으로 매일 작동합니다.",
  );
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (!configuration) return;
    setWarmup(configuration.policies?.["boot-lead-seconds"] ?? 120);
    setCleanup(configuration.policies?.["shutdown-grace-seconds"] ?? 60);
    setItems(configuration.power_keep_schedules || []);
  }, [configuration]);
  const update = (index, key, value) =>
    setItems((current) =>
      current.map((item, itemIndex) =>
        itemIndex === index ? { ...item, [key]: value } : item,
      ),
    );
  const submit = async (event) => {
    event.preventDefault();
    if (
      ![warmup, cleanup].every(
        (value) =>
          Number.isInteger(Number(value)) &&
          Number(value) >= 0 &&
          Number(value) <= 86400,
      )
    )
      return notify("여유 시간은 0~86400초 사이의 정수여야 합니다.", true);
    try {
      items.forEach((item) => {
        if (captureTimeSeconds(item.end) <= captureTimeSeconds(item.start))
          throw new Error("전원 유지 종료 시각은 시작 시각보다 늦어야 합니다.");
      });
    } catch (error) {
      return notify(error.message, true);
    }
    if (!confirm("전원 설정을 적용할까요?")) return;
    setBusy(true);
    try {
      const result = await api("/api/power-settings", {
        method: "PUT",
        body: JSON.stringify({
          warmup_seconds: Number(warmup),
          cleanup_seconds: Number(cleanup),
          keep_schedules: items,
        }),
      });
      setNote(`전원 설정 적용 완료 · 설정 #${result.revision.id}`);
      notify("전원 설정을 적용했습니다.");
      await refresh();
    } catch (error) {
      notify(error.message, true);
    } finally {
      setBusy(false);
    }
  };
  return (
    <article className="panel settings-card power-settings-card power-block">
      <div className="panel-title">
        <div>
          <h2>전원 관리</h2>
          <span>작업 전후 여유 시간과 별도 전원 유지 일정을 관리합니다.</span>
        </div>
      </div>
      <form onSubmit={submit}>
        <div className="form-grid">
          <label className="field">
            <span>작동 전 여유 시간(초)</span>
            <input
              type="number"
              min="0"
              max="86400"
              value={warmup}
              onChange={(event) => setWarmup(Number(event.target.value))}
            />
          </label>
          <label className="field">
            <span>작동 후 여유 시간(초)</span>
            <input
              type="number"
              min="0"
              max="86400"
              value={cleanup}
              onChange={(event) => setCleanup(Number(event.target.value))}
            />
          </label>
        </div>
        <div className="subsection-heading power-keep-heading">
          <div>
            <strong>전원 유지 시간</strong>
            <span>촬영 일정과 별개로 매일 반복됩니다.</span>
          </div>
          <button
            type="button"
            className="ghost"
            onClick={() =>
              setItems((current) => [
                ...current,
                {
                  id: `daily-power-keep-${Date.now()}`,
                  start: "09:00:00",
                  end: "10:00:00",
                  timezone: configuration?.timezone || "Asia/Seoul",
                },
              ])
            }
          >
            + 전원 유지 추가
          </button>
        </div>
        <div className="power-keep-list">
          {items.length ? (
            items.map((item, index) => (
              <div className="power-keep-row" key={item.id}>
                <label className="field">
                  <span>시작 시각</span>
                  <input
                    type="time"
                    step="1"
                    value={item.start}
                    onChange={(event) =>
                      update(index, "start", event.target.value)
                    }
                  />
                </label>
                <span className="power-keep-separator">부터</span>
                <label className="field">
                  <span>종료 시각</span>
                  <input
                    type="time"
                    step="1"
                    value={item.end}
                    onChange={(event) =>
                      update(index, "end", event.target.value)
                    }
                  />
                </label>
                <button
                  type="button"
                  className="text-button danger-text"
                  onClick={() =>
                    setItems((current) =>
                      current.filter((_, itemIndex) => itemIndex !== index),
                    )
                  }
                >
                  삭제
                </button>
              </div>
            ))
          ) : (
            <div className="friendly-empty">
              <strong>별도 전원 유지 시간이 없습니다.</strong>
            </div>
          )}
        </div>
        <div className="settings-actions storage-apply-actions">
          <span>{note}</span>
          <button disabled={busy}>전원 설정 적용</button>
        </div>
      </form>
    </article>
  );
}
