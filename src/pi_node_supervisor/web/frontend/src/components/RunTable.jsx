import { Empty, Status } from "./Common.jsx";
import { fmt } from "../utils.js";

export default function RunTable({ items }) {
  if (!items.length) return <Empty />;
  return (
    <table>
      <thead>
        <tr>
          <th>상태</th>
          <th>작업 / run</th>
          <th>노드</th>
          <th>실행 시각</th>
          <th>결과</th>
        </tr>
      </thead>
      <tbody>
        {items.map((run) => {
          const indexed = run.result?.indexed_data_count;
          const result =
            run.error ||
            (run.status === "succeeded"
              ? `촬영 완료${Number.isInteger(indexed) ? ` · 파일 ${indexed}개` : ""}`
              : run.external_id || "—");
          return (
            <tr key={run.run_id}>
              <td>
                <Status value={run.status} />
              </td>
              <td>
                <strong>
                  {run.activity_id.startsWith("native-")
                    ? "정기 촬영"
                    : run.activity_id}
                </strong>
                <br />
                <code>{run.run_id.slice(0, 12)}</code>
              </td>
              <td>{run.node_id}</td>
              <td>{fmt(run.scheduled_at)}</td>
              <td>{result}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
