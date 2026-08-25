import { useEffect, useRef, useState } from "react";

import { api, token } from "../api-client.js";
import { Empty, PageHeading, Pagination } from "../components/Common.jsx";
import { useSupervisor } from "../SupervisorContext.jsx";
import { fmt, formatBytes } from "../utils.js";

function DataViewer({ item, onClose }) {
  const ref = useRef(null);
  const urlRef = useRef(null);
  const [src, setSrc] = useState(null);
  const [error, setError] = useState(null);
  useEffect(() => {
    const controller = new AbortController();
    ref.current?.showModal();
    (async () => {
      try {
        const response = await fetch(
          `/api/data/content?data_id=${encodeURIComponent(item.data_id)}&download=false`,
          {
            headers: { "X-Supervisor-Token": token() },
            signal: controller.signal,
          },
        );
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const url = URL.createObjectURL(await response.blob());
        urlRef.current = url;
        setSrc(url);
      } catch (reason) {
        if (reason.name !== "AbortError") setError(reason.message);
      }
    })();
    return () => {
      controller.abort();
      if (urlRef.current) URL.revokeObjectURL(urlRef.current);
    };
  }, [item.data_id]);
  return (
    <dialog ref={ref} className="data-view-dialog" onClose={onClose}>
      <div className="data-view-heading">
        <strong>
          {item.storage_location?.split("/").at(-1) || "원본 이미지"}
        </strong>
        <button className="ghost" onClick={() => ref.current.close()}>
          닫기
        </button>
      </div>
      <div className="data-view-canvas">
        {src ? (
          <img src={src} alt="저장된 원본 이미지" />
        ) : (
          <Empty error={Boolean(error)}>
            {error || "원본을 불러오는 중입니다."}
          </Empty>
        )}
      </div>
    </dialog>
  );
}

export default function DataPage() {
  const { data, loadData, refresh, notify } = useSupervisor();
  const [viewer, setViewer] = useState(null);
  const change = (offset, limit) =>
    loadData(offset, limit).catch((error) => notify(error.message, true));
  const sync = async () => {
    try {
      const result = await api("/api/data/sync", { method: "POST" });
      notify(
        result.new_data
          ? `새 데이터 ${result.new_data}개를 반영했습니다.`
          : "이미 최신 상태입니다.",
      );
      await refresh();
    } catch (error) {
      notify(error.message, true);
    }
  };
  const download = async (item) => {
    try {
      const response = await fetch(
        `/api/data/content?data_id=${encodeURIComponent(item.data_id)}&download=true`,
        { headers: { "X-Supervisor-Token": token() } },
      );
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement("a");
      link.href = url;
      link.download = item.storage_location?.split("/").at(-1) || "original";
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 60000);
    } catch (error) {
      notify(error.message, true);
    }
  };
  return (
    <section className="view active">
      <PageHeading
        eyebrow="DATA CATALOG"
        title="수집 데이터"
        description="카메라 촬영 파일과 토양 센서 측정 기록을 자동으로 반영합니다."
        actions={
          <button className="ghost" onClick={sync}>
            데이터 갱신
          </button>
        }
      />
      <article className="panel data-panel">
        {data.items.length ? (
          <table className="data-table">
            <thead>
              <tr>
                <th>생성 시각</th>
                <th>노드</th>
                <th>크기</th>
                <th>저장 위치</th>
                <th>원본</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((item) => {
                const location = item.storage_location || item.summary || "—";
                const dng =
                  item.media_type === "image/x-adobe-dng" ||
                  /\.dng$/i.test(location);
                return (
                  <tr key={item.data_id}>
                    <td>{fmt(item.captured_at)}</td>
                    <td>
                      <strong>{item.node_id}</strong>
                      {dng && <small>DNG RAW</small>}
                    </td>
                    <td>{formatBytes(item.size)}</td>
                    <td>
                      <code className="data-location">{location}</code>
                    </td>
                    <td>
                      {item.original_available ? (
                        <div className="data-actions">
                          {!dng && (
                            <button
                              className="ghost"
                              onClick={() => setViewer(item)}
                            >
                              보기
                            </button>
                          )}
                          <button
                            className="ghost"
                            onClick={() => download(item)}
                          >
                            다운로드
                          </button>
                        </div>
                      ) : (
                        <span className="help">접근 불가</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        ) : (
          <Empty>표시할 수집 데이터가 없습니다.</Empty>
        )}
        <Pagination page={data} onChange={change} />
      </article>
      {viewer && <DataViewer item={viewer} onClose={() => setViewer(null)} />}
    </section>
  );
}
