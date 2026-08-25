import { useEffect, useState } from "react";

import { api } from "../../api-client.js";
import { useSupervisor } from "../../SupervisorContext.jsx";
import { DEFAULT_CONFIGURATION } from "../../utils.js";

export default function AdvancedConfigEditor() {
  const { configuration, refresh, notify } = useSupervisor();
  const [text, setText] = useState(
    JSON.stringify(DEFAULT_CONFIGURATION, null, 2),
  );
  useEffect(() => {
    if (configuration) setText(JSON.stringify(configuration, null, 2));
  }, [configuration]);
  const parse = () => {
    try {
      return JSON.parse(text);
    } catch {
      throw new Error("먼저 편집기의 JSON 형식을 확인하세요.");
    }
  };
  const action = async (callback) => {
    try {
      await callback();
    } catch (error) {
      notify(error.message, true);
    }
  };
  return (
    <details className="panel advanced-settings">
      <summary>고급 설정 · JSON 직접 편집</summary>
      <p className="help">일반 화면에 없는 기능이 필요할 때만 사용하세요.</p>
      <div className="button-row">
        <button
          className="ghost"
          onClick={() =>
            action(async () =>
              setText(JSON.stringify(await api("/api/config/draft"), null, 2)),
            )
          }
        >
          저장된 초안
        </button>
        <button
          className="ghost"
          onClick={() =>
            action(async () => {
              const value = parse();
              value.capture_node_settings = await api(
                "/api/settings/capture-nodes",
              );
              setText(JSON.stringify(value, null, 2));
              notify("현재 카메라 설정을 가져왔습니다.");
            })
          }
        >
          현재 카메라 설정 가져오기
        </button>
        <button
          className="ghost"
          onClick={() =>
            action(async () => {
              await api("/api/plan/preview", { method: "POST", body: text });
              notify("계획 검증이 완료되었습니다.");
            })
          }
        >
          JSON 검증
        </button>
        <button
          className="ghost"
          onClick={() =>
            action(async () => {
              await api("/api/config/draft", { method: "PUT", body: text });
              notify("Draft를 저장했습니다.");
            })
          }
        >
          초안 저장
        </button>
        <button
          className="danger"
          onClick={() =>
            action(async () => {
              if (!confirm("검증된 Draft를 새 Active revision으로 적용할까요?"))
                return;
              const result = await api("/api/revisions/apply", {
                method: "POST",
              });
              notify(`Revision #${result.id}을 적용했습니다.`);
              await refresh();
            })
          }
        >
          JSON 적용
        </button>
      </div>
      <textarea
        id="config-editor"
        spellCheck="false"
        aria-label="고급 업무 설정 JSON"
        value={text}
        onChange={(event) => setText(event.target.value)}
      />
    </details>
  );
}
