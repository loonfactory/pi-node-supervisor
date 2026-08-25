import { useEffect, useState } from "react";

import { api } from "../../api-client.js";
import { useSupervisor } from "../../SupervisorContext.jsx";

export default function SensorSettings() {
  const { sensors, loadSensors, notify } = useSupervisor();
  const [nodeId, setNodeId] = useState("");
  const [serial, setSerial] = useState(null);
  const [storage, setStorage] = useState(null);
  const [mirrorsText, setMirrorsText] = useState("[]");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (!nodeId && sensors.settings.length)
      setNodeId(sensors.settings[0].node_id);
  }, [nodeId, sensors.settings]);
  useEffect(() => {
    const item = sensors.settings.find((value) => value.node_id === nodeId);
    if (!item) return;
    setSerial({ ...item.sensor_serial });
    setStorage({ ...item.sensor_storage });
    setMirrorsText(JSON.stringify(item.sensor_storage.mirrors || [], null, 2));
    setNote(
      sensors.restartRequired
        ? "저장 완료 · 서비스 재시작 전까지 기존 설정으로 실행됩니다."
        : item.health?.healthy
          ? "직렬 연결 정상"
          : item.health?.error || "연결 상태를 확인하세요.",
    );
  }, [nodeId, sensors.restartRequired, sensors.settings]);
  const submit = async (event) => {
    event.preventDefault();
    let mirrors;
    try {
      mirrors = JSON.parse(mirrorsText);
      if (!Array.isArray(mirrors))
        throw new Error("복제 대상은 JSON 배열이어야 합니다.");
    } catch (error) {
      return notify(error.message, true);
    }
    if (!serial.port || !storage.local_directory)
      return notify("직렬 포트와 로컬 CSV 폴더를 입력해 주세요.", true);
    if (!confirm(`${nodeId}의 센서 설정을 저장할까요?`)) return;
    setBusy(true);
    try {
      const result = await api(
        `/api/sensor-settings/${encodeURIComponent(nodeId)}`,
        {
          method: "PUT",
          body: JSON.stringify({
            sensor_serial: serial,
            sensor_storage: { ...storage, mirrors },
          }),
        },
      );
      setNote(
        `저장 완료 · ${result.backup}에서 복구 가능 · 서비스 재시작 필요`,
      );
      notify("토양 센서 설정을 저장했습니다.");
      await loadSensors();
    } catch (error) {
      notify(error.message, true);
    } finally {
      setBusy(false);
    }
  };
  if (!serial || !storage)
    return (
      <article className="panel settings-card soil-sensor-block">
        <div className="empty">설정 가능한 토양 센서가 없습니다.</div>
      </article>
    );
  const serialValue =
    (key, numeric = false) =>
    (event) =>
      setSerial((value) => ({
        ...value,
        [key]: numeric ? Number(event.target.value) : event.target.value,
      }));
  const storageValue =
    (key, numeric = false) =>
    (event) =>
      setStorage((value) => ({
        ...value,
        [key]: numeric ? Number(event.target.value) : event.target.value,
      }));
  return (
    <article className="panel settings-card sensor-storage-card soil-sensor-block">
      <div className="panel-title">
        <div>
          <h2>토양 센서 연결과 CSV 저장소</h2>
          <span>CWT 직렬 연결과 로컬 CSV·원격 복제를 설정합니다.</span>
        </div>
      </div>
      <label className="field compact-field">
        <span>적용 대상</span>
        <select
          value={nodeId}
          onChange={(event) => setNodeId(event.target.value)}
        >
          {sensors.settings.map((item) => (
            <option key={item.node_id}>{item.node_id}</option>
          ))}
        </select>
      </label>
      <form onSubmit={submit}>
        <div className="form-grid">
          <label className="field">
            <span>드라이버</span>
            <input value={serial.driver} readOnly />
          </label>
          <label className="field">
            <span>직렬 포트</span>
            <input value={serial.port} onChange={serialValue("port")} />
          </label>
          <label className="field">
            <span>Baudrate</span>
            <input
              type="number"
              value={serial.baudrate}
              onChange={serialValue("baudrate", true)}
            />
          </label>
          <label className="field">
            <span>Slave ID</span>
            <input
              type="number"
              min="1"
              max="254"
              value={serial.slave_id}
              onChange={serialValue("slave_id", true)}
            />
          </label>
          <label className="field">
            <span>응답 제한(초)</span>
            <input
              type="number"
              step=".1"
              value={serial.timeout_seconds}
              onChange={serialValue("timeout_seconds", true)}
            />
          </label>
          <label className="field">
            <span>로컬 CSV 원본 폴더</span>
            <input
              value={storage.local_directory}
              onChange={storageValue("local_directory")}
            />
          </label>
          <label className="field">
            <span>재시도 시작(초)</span>
            <input
              type="number"
              value={storage.retry_base_seconds}
              onChange={storageValue("retry_base_seconds", true)}
            />
          </label>
          <label className="field">
            <span>재시도 최대(초)</span>
            <input
              type="number"
              value={storage.retry_max_seconds}
              onChange={storageValue("retry_max_seconds", true)}
            />
          </label>
        </div>
        <label className="field">
          <span>file/SFTP/FTPS 복제 대상(JSON 배열)</span>
          <textarea
            spellCheck="false"
            value={mirrorsText}
            onChange={(event) => setMirrorsText(event.target.value)}
          />
        </label>
        <p className="field-help">
          비밀번호 값 대신 환경변수 이름만 저장합니다.
        </p>
        <div className="settings-actions storage-apply-actions">
          <span>{note}</span>
          <button disabled={busy}>센서 설정 저장</button>
        </div>
      </form>
    </article>
  );
}
