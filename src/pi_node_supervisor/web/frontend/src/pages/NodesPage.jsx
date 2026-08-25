import { Empty, PageHeading, Status } from "../components/Common.jsx";
import { useSupervisor } from "../SupervisorContext.jsx";

export default function NodesPage() {
  const { nodes } = useSupervisor();
  return <section className="view active">
    <PageHeading eyebrow="MANAGED DEVICES" title="연결된 장치" description="Supervisor v2가 직접 관리하는 카메라 프로세스와 센서입니다." />
    <div className="node-grid">{nodes.length ? nodes.map((node) => <article className="node-card" key={node.id}>
      <div className="panel-title"><h2>{node.id}</h2><Status value={node.active ? "healthy" : "unhealthy"} /></div>
      <dl><dt>종류</dt><dd>{node.kind}</dd>{node.endpoint && <><dt>Endpoint</dt><dd>{node.endpoint}</dd></>}{node.driver && <><dt>드라이버</dt><dd>{node.driver}</dd></>}{node.port && <><dt>포트</dt><dd>{node.port}</dd></>}</dl>
    </article>) : <Empty>등록된 장치가 없습니다.</Empty>}</div>
  </section>;
}
