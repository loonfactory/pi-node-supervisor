import { useEffect, useState } from "react";

import { api } from "../../api-client.js";
import { useSupervisor } from "../../SupervisorContext.jsx";
import { DEFAULT_SENSOR_RANGE, generateTimes } from "../../utils.js";
import { RangeGenerator } from "./ScheduleFields.jsx";

const fallback = {
  mode: "daily",
  daily_times: generateTimes(DEFAULT_SENSOR_RANGE, "수집"),
  timezone: "Asia/Seoul",
};
export default function SensorScheduleSettings() {
  const { nodes, refresh, notify } = useSupervisor();
  const sensorNodes = nodes.filter((node) =>
    node.activity_types?.includes("sensor-collection"),
  );
  const [target, setTarget] = useState("common");
  const [schedule, setSchedule] = useState(fallback);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    api("/api/sensor-schedules")
      .then((response) => {
        const map = new Map(response.items.map((item) => [item.node, item]));
        const next =
          (target === "common"
            ? map.get(response.sensor_ids[0])
            : map.get(target)) || fallback;
        setSchedule({ ...next, daily_times: next.daily_times || [] });
        setNote(
          target === "common"
            ? "모든 토양 센서에 같은 저장 시각을 적용합니다."
            : `${target}에만 적용합니다.`,
        );
      })
      .catch((error) => setNote(error.message));
  }, [target]);
  const submit = async (event) => {
    event.preventDefault();
    const next =
      schedule.mode === "disabled"
        ? { ...schedule, daily_times: [] }
        : schedule;
    if (new Set(next.daily_times).size !== next.daily_times.length)
      return notify("같은 수집 시각을 두 번 등록할 수 없습니다.", true);
    if (
      !confirm(
        `${target === "common" ? "모든 토양 센서" : target}의 수집 일정을 적용할까요?`,
      )
    )
      return;
    setBusy(true);
    try {
      const result = await api("/api/sensor-schedules", {
        method: "PUT",
        body: JSON.stringify({ target, settings: next }),
      });
      setNote(`토양 센서 일정 적용 완료 · 설정 #${result.revision.id}`);
      notify("토양 센서 일정을 적용했습니다.");
      await refresh();
    } catch (error) {
      notify(error.message, true);
    } finally {
      setBusy(false);
    }
  };
  return (
    <article className="panel settings-card capture-schedule-card soil-sensor-block">
      <div className="panel-title">
        <div>
          <h2>토양 센서 수집 일정</h2>
          <span>
            저장된 시각마다 온도와 습도를 읽고 전원 계획에 반영합니다.
          </span>
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
              <option value="common">모든 토양 센서 · 공통</option>
              {sensorNodes.map((node) => (
                <option value={node.id} key={node.id}>
                  {node.id} · 개별
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>자동 수집 방식</span>
            <select
              value={schedule.mode}
              onChange={(event) =>
                setSchedule((value) => ({ ...value, mode: event.target.value }))
              }
            >
              <option value="disabled">사용 안 함</option>
              <option value="daily">매일 지정한 시각</option>
            </select>
          </label>
        </div>
        {schedule.mode === "daily" && (
          <RangeGenerator
            initial={DEFAULT_SENSOR_RANGE}
            values={schedule.daily_times}
            onChange={(daily_times) =>
              setSchedule((value) => ({ ...value, daily_times }))
            }
            label="수집"
          />
        )}
        {schedule.mode === "daily" && !schedule.daily_times.length && (
          <p className="settings-warning">
            수집 시각이 없습니다. 자동 수집이 비활성화됩니다.
          </p>
        )}
        <div className="settings-actions storage-apply-actions">
          <span>{note}</span>
          <button disabled={busy}>수집 일정 적용</button>
        </div>
      </form>
    </article>
  );
}
