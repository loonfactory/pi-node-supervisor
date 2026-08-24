import { api } from "./api-client.js";
import { $, toast } from "./presentation.js";

const DEFAULT_CONFIGURATION = {
  schema_version: 1,
  timezone: "Asia/Seoul",
  policies: {
    "boot-lead-seconds": 120,
    "shutdown-grace-seconds": 60,
    "merge-power-gaps-shorter-than-seconds": 300,
    "minimum-lead-seconds": 60,
    "clock-tolerance-seconds": 5,
    "recurrence-horizon-hours": 168,
  },
  activities: [],
  capture_schedules: [],
  sensor_schedules: [],
  power_keep_schedules: [],
  capture_node_settings: {
    common: {},
    nodes: {},
  },
};

function editorDocument() {
  return JSON.parse($("#config-editor").value);
}

function showDocument(document) {
  $("#config-editor").value = JSON.stringify(document, null, 2);
}

export function initializeConfigurationEditor(refresh) {
  showDocument(DEFAULT_CONFIGURATION);

  $("#preview-plan").addEventListener("click", async () => {
    try {
      await api("/api/plan/preview", {
        method: "POST",
        body: $("#config-editor").value,
      });
      toast("계획 검증이 완료되었습니다.");
    } catch (error) {
      toast(error.message, true);
    }
  });

  $("#save-draft").addEventListener("click", async () => {
    try {
      await api("/api/config/draft", {
        method: "PUT",
        body: $("#config-editor").value,
      });
      toast("Draft를 저장했습니다.");
    } catch (error) {
      toast(error.message, true);
    }
  });

  $("#load-draft").addEventListener("click", async () => {
    try {
      showDocument(await api("/api/config/draft"));
    } catch (error) {
      toast(error.message, true);
    }
  });

  $("#load-capture-node-settings").addEventListener("click", async () => {
    try {
      const configuration = editorDocument();
      configuration.capture_node_settings = await api("/api/settings/capture-nodes");
      showDocument(configuration);
      toast("현재 카메라 설정을 편집기에 가져왔습니다. 저장하거나 적용하기 전 내용을 확인하세요.");
    } catch (error) {
      toast(error instanceof SyntaxError
        ? "먼저 편집기의 JSON 형식을 확인하세요."
        : error.message, true);
    }
  });

  $("#apply-draft").addEventListener("click", async () => {
    if (!confirm("검증된 Draft를 새 Active revision으로 적용할까요?")) return;
    try {
      const result = await api("/api/revisions/apply", { method: "POST" });
      toast(`Revision #${result.id}을 적용했습니다.`);
      await refresh();
    } catch (error) {
      toast(error.message, true);
    }
  });
}
