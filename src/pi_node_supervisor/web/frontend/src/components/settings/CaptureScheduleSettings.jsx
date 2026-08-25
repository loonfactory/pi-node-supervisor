import { useEffect, useState } from "react";

import { api } from "../../api-client.js";
import { useSupervisor } from "../../SupervisorContext.jsx";
import {
  DEFAULT_CAPTURE_RANGE,
  generateTimes,
  intervalSeconds,
  splitInterval,
} from "../../utils.js";
import { RangeGenerator } from "./ScheduleFields.jsx";

const fallback = {
  mode: "daily",
  interval_seconds: null,
  daily_times: generateTimes(DEFAULT_CAPTURE_RANGE),
  timezone: "Asia/Seoul",
};

export default function CaptureScheduleSettings() {
  const { nodes, refresh, notify } = useSupervisor();
  const cameras = nodes.filter((node) => node.kind === "capture-node");
  const [target, setTarget] = useState("common");
  const [schedule, setSchedule] = useState(fallback);
  const [parts, setParts] = useState(splitInterval(0));
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (!target) return;
    setNote("촬영 일정을 불러오는 중입니다…");
    api("/api/capture-schedules")
      .then((response) => {
        const map = new Map(response.items.map((item) => [item.node, item]));
        const value =
          target === "common"
            ? map.get(response.camera_ids[0])
            : map.get(target);
        const next = value || fallback;
        setSchedule({ ...next, daily_times: next.daily_times || [] });
        setParts(splitInterval(next.interval_seconds));
        setNote(
          target === "common"
            ? "모든 카메라에 같은 촬영 일정을 적용합니다."
            : `${target}에만 적용합니다.`,
        );
      })
      .catch((error) =>
        setNote(`설정을 불러오지 못했습니다: ${error.message}`),
      );
  }, [target]);
  const submit = async (event) => {
    event.preventDefault();
    const next =
      schedule.mode === "interval"
        ? {
            ...schedule,
            interval_seconds: intervalSeconds(parts),
            daily_times: [],
          }
        : schedule.mode === "disabled"
          ? { ...schedule, interval_seconds: null, daily_times: [] }
          : { ...schedule, interval_seconds: null };
    if (next.mode === "interval" && next.interval_seconds <= 0)
      return notify("0보다 큰 촬영 간격을 입력해 주세요.", true);
    if (new Set(next.daily_times).size !== next.daily_times.length)
      return notify("같은 촬영 시각을 두 번 등록할 수 없습니다.", true);
    if (
      !confirm(
        `${target === "common" ? "모든 카메라" : target}의 촬영 일정을 적용할까요?`,
      )
    )
      return;
    setBusy(true);
    try {
      const result = await api("/api/capture-schedules", {
        method: "PUT",
        body: JSON.stringify({ target, settings: next }),
      });
      setNote(`촬영 일정 적용 완료 · 설정 #${result.revision.id}`);
      notify("촬영 일정을 적용했습니다.");
      await refresh();
    } catch (error) {
      notify(error.message, true);
    } finally {
      setBusy(false);
    }
  };
  return (
    <article className="panel settings-card capture-schedule-card camera-block">
      <div className="panel-title">
        <div>
          <h2>카메라 촬영 일정</h2>
          <span>카메라의 반복 촬영 시간과 간격을 설정합니다.</span>
        </div>
      </div>
      <form onSubmit={submit}>
        <div className="form-grid schedule-mode-grid">
          <label className="field">
            <span>적용 대상</span>
            <select
              value={target}
              onChange={(event) => setTarget(event.target.value)}
            >
              <option value="common">모든 카메라 · 공통</option>
              {cameras.map((node) => (
                <option value={node.id} key={node.id}>
                  {node.id} · 개별
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>자동 촬영 방식</span>
            <select
              value={schedule.mode}
              onChange={(event) =>
                setSchedule((value) => ({ ...value, mode: event.target.value }))
              }
            >
              <option value="disabled">사용 안 함</option>
              <option value="interval">일정 간격마다</option>
              <option value="daily">매일 지정한 시각</option>
            </select>
          </label>
        </div>
        {schedule.mode === "interval" && (
          <div className="capture-duration-editor">
            <div className="duration-grid">
              {[
                ["days", "일", 9999],
                ["hours", "시간", 23],
                ["minutes", "분", 59],
                ["seconds", "초", 59.99],
              ].map(([key, label, max]) => (
                <label className="field" key={key}>
                  <input
                    type="number"
                    min="0"
                    max={max}
                    step={key === "seconds" ? ".01" : "1"}
                    value={parts[key]}
                    onChange={(event) =>
                      setParts((value) => ({
                        ...value,
                        [key]: Number(event.target.value),
                      }))
                    }
                  />
                  <span>{label}</span>
                </label>
              ))}
            </div>
          </div>
        )}
        {schedule.mode === "daily" && (
          <RangeGenerator
            initial={DEFAULT_CAPTURE_RANGE}
            values={schedule.daily_times}
            onChange={(daily_times) =>
              setSchedule((value) => ({ ...value, daily_times }))
            }
            label="촬영"
          />
        )}
        {schedule.mode === "daily" && !schedule.daily_times.length && (
          <p className="settings-warning">
            촬영 시각이 없습니다. 카메라 자동 촬영이 비활성화됩니다.
          </p>
        )}
        <div className="settings-actions storage-apply-actions">
          <span>{note}</span>
          <button disabled={busy} type="submit">
            촬영 일정 적용
          </button>
        </div>
      </form>
    </article>
  );
}
