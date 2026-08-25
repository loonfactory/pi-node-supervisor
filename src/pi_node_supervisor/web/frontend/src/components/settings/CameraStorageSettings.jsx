import { useEffect, useState } from "react";

import { api } from "../../api-client.js";
import { useSupervisor } from "../../SupervisorContext.jsx";

export default function CameraStorageSettings() {
  const { nodes, refresh, notify } = useSupervisor();
  const cameras = nodes.filter((node) => node.kind === "capture-node");
  const [target, setTarget] = useState("common");
  const [text, setText] = useState("[]");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    const ids = target === "common" ? cameras.map((node) => node.id) : [target];
    if (!ids.length) {
      setText("[]");
      return;
    }
    setNote("저장소 설정을 불러오는 중입니다…");
    Promise.all(
      ids.map((id) =>
        api(`/api/nodes/${encodeURIComponent(id)}/configuration`),
      ),
    )
      .then((configurations) => {
        const first = configurations[0];
        const retention = new Map(
          (first.maintenance?.retention || []).map((item) => [
            item.target,
            item.max_age_days,
          ]),
        );
        const storages = (first.storages || []).map((item) => ({
          ...item,
          retention_days: retention.get(item.name) || null,
        }));
        if (target === "common")
          storages.forEach((item) => {
            if (item.directory)
              item.directory = item.directory.replaceAll(
                first.node_id,
                "{camera}",
              );
            if (item.remote_directory)
              item.remote_directory = item.remote_directory.replaceAll(
                first.node_id,
                "{camera}",
              );
          });
        setText(JSON.stringify(storages, null, 2));
        setNote(
          target === "common"
            ? "모든 카메라에 공통 적용됩니다. {camera}는 카메라 이름으로 바뀝니다."
            : `${target}에만 개별 적용됩니다.`,
        );
      })
      .catch((error) =>
        setNote(`설정을 불러오지 못했습니다: ${error.message}`),
      );
  }, [target, nodes]);
  const submit = async (event) => {
    event.preventDefault();
    let storages;
    try {
      storages = JSON.parse(text);
      if (!Array.isArray(storages))
        throw new Error("저장소는 JSON 배열이어야 합니다.");
    } catch (error) {
      return notify(error.message, true);
    }
    if (
      !confirm(
        `${target === "common" ? "모든 카메라" : target}의 저장소를 적용할까요?`,
      )
    )
      return;
    const items = storages.map(({ retention_days, ...item }) => item);
    const retention = storages
      .filter((item) => item.type === "file" && item.retention_days)
      .map((item) => ({
        target: item.name,
        max_age_days: Number(item.retention_days),
      }));
    setBusy(true);
    try {
      const path =
        target === "common"
          ? "/api/settings/cameras/storages"
          : `/api/nodes/${encodeURIComponent(target)}/storages`;
      const result = await api(path, {
        method: "PUT",
        body: JSON.stringify({ items, retention }),
      });
      setNote(
        `적용 완료 · ${(result.restarted_nodes || []).join(", ")} 정상 재시작`,
      );
      notify("카메라 저장소를 적용했습니다.");
      await refresh();
    } catch (error) {
      notify(error.message, true);
    } finally {
      setBusy(false);
    }
  };
  return (
    <article className="panel settings-card storage-card camera-block">
      <div className="panel-title">
        <div>
          <h2>카메라 저장소</h2>
          <span>file/SFTP/FTPS 저장 위치와 파일 보존 기간을 관리합니다.</span>
        </div>
      </div>
      <label className="field compact-field">
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
      <form onSubmit={submit}>
        <label className="field">
          <span>저장소 목록(JSON 배열)</span>
          <textarea
            spellCheck="false"
            value={text}
            onChange={(event) => setText(event.target.value)}
          />
        </label>
        <p className="field-help">
          각 항목에 `retention_days`를 추가하면 file 저장소의 자동 삭제 기간으로
          적용됩니다.
        </p>
        <div className="settings-actions storage-apply-actions">
          <span>{note}</span>
          <button disabled={busy}>저장소 적용</button>
        </div>
      </form>
    </article>
  );
}
