import { Empty, PageHeading, Status } from "../components/Common.jsx";
import { useSupervisor } from "../SupervisorContext.jsx";

export default function NodesPage() {
  const { nodes } = useSupervisor();
  return (
    <section className="view active">
      <PageHeading
        eyebrow="MANAGED NODES"
        title="연결된 장치"
        description="Supervisor가 관리하는 카메라와 실행 기능의 연결 상태입니다."
      />
      <div className="node-grid">
        {nodes.length ? (
          nodes.map((node) => (
            <article className="node-card" key={node.id}>
              <div className="panel-title">
                <h2>{node.id}</h2>
                <Status
                  value={node.health?.healthy ? "healthy" : "unhealthy"}
                />
              </div>
              <dl>
                <dt>종류</dt>
                <dd>{node.kind}</dd>
                <dt>Endpoint</dt>
                <dd>{node.endpoint || "—"}</dd>
                <dt>업무</dt>
                <dd>{node.activity_types.join(", ")}</dd>
                <dt>프로필</dt>
                <dd>{node.profiles.join(", ") || "—"}</dd>
                <dt>상태</dt>
                <dd>
                  {node.health?.error || node.health?.status || "어댑터 없음"}
                </dd>
              </dl>
            </article>
          ))
        ) : (
          <Empty>등록된 노드가 없습니다.</Empty>
        )}
      </div>
    </section>
  );
}
