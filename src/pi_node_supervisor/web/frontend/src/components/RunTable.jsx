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
          const saved = run.result?.storageSucceeded;
          const attempted = run.result?.storageAttempted;
          const result =
            run.error ||
            (run.status === "succeeded"
              ? `완료${Number.isInteger(saved) ? ` · 저장 ${saved}/${attempted}` : ""}`
              : "—");
          return (
            <tr key={run.runId}>
              <td>
                <Status value={run.status} />
              </td>
              <td>
                <strong>
                  {run.deviceKind === "camera" ? "카메라 촬영" : "센서 수집"}
                </strong>
                <br />
                <code>{run.runId.slice(0, 12)}</code>
              </td>
              <td>{run.deviceId}</td>
              <td>{fmt(run.scheduledAt)}</td>
              <td>{result}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
