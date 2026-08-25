import { Empty, PageHeading, Pagination } from "../components/Common.jsx";
import { useSupervisor } from "../SupervisorContext.jsx";
import { fmt, formatBytes } from "../utils.js";

export default function DataPage() {
  const { data, loadData, notify } = useSupervisor();
  const change = (offset, limit) => loadData(offset, limit).catch((error) => notify(error.message, true));
  return <section className="view active">
    <PageHeading eyebrow="DATA CATALOG" title="촬영 산출물" description="Supervisor가 CaptureNode에서 검증한 산출물 메타데이터입니다. 실제 파일은 설정된 저장 대상에서 관리합니다." actions={<button className="ghost" onClick={() => change(data.offset, data.limit)}>갱신</button>} />
    <article className="panel data-panel">
      {data.items.length ? <table className="data-table"><thead><tr><th>촬영 시각</th><th>장치</th><th>파일</th><th>형식</th><th>크기</th><th>SHA-256</th></tr></thead><tbody>
        {data.items.map((item) => <tr key={item.artifactId}><td>{fmt(item.capturedAt)}</td><td><strong>{item.deviceId}</strong></td><td><code>{item.name}</code></td><td>{item.mediaType}</td><td>{formatBytes(item.size)}</td><td><code>{item.sha256.slice(0, 16)}…</code></td></tr>)}
      </tbody></table> : <Empty>표시할 촬영 산출물이 없습니다.</Empty>}
      <Pagination page={data} onChange={change} />
    </article>
  </section>;
}
