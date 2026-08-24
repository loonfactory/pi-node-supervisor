import { useEffect } from "react";

export default function App() {
  useEffect(() => {
    void import("./legacy-app.js");
  }, []);

  return (
    <>
      <header className="topbar">
      <div className="brand">
        <span className="brand-mark">π</span>
        <div><strong>Node Supervisor</strong><small>RASPBERRY PI · LOCAL CONTROL</small></div>
      </div>
      <div className="top-actions">
        <span id="connection" className="pill muted">연결 확인 중</span>
        <button id="token-button" className="ghost">접속 토큰</button>
        <button id="refresh-button">갱신</button>
      </div>
    </header>

      <div className="shell">
      <nav className="sidebar" aria-label="주 메뉴">
        <button className="nav-item active" data-view="dashboard">대시보드</button>
        <button className="nav-item" data-view="cameras">카메라</button>
        <button className="nav-item" data-view="sensors">토양 센서</button>
        <button className="nav-item" data-view="settings">설정</button>
        <button className="nav-item" data-view="events">이벤트</button>
        <button className="nav-item" data-view="data">데이터</button>
        <button className="nav-item" data-view="nodes">노드</button>
      </nav>

      <main>
        <section id="dashboard" className="view active">
          <div className="page-heading"><div><p className="eyebrow">SYSTEM OVERVIEW</p><h1>장치 운영 현황</h1></div><button id="refresh-dashboard">노드 상태 갱신</button></div>
          <div id="metric-grid" className="metric-grid"></div>
          <div className="two-column">
            <article className="panel"><div className="panel-title"><h2>전원 타임라인</h2><span>Active revision</span></div><div id="power-timeline" className="timeline empty">활성 계획이 없습니다.</div></article>
            <article className="panel"><div className="panel-title"><h2>노드 상태</h2><span>Live</span></div><div id="node-summary" className="stack empty">등록된 실행 노드가 없습니다.</div></article>
          </div>
          <article className="panel"><div className="panel-title"><h2>최근 촬영</h2><button className="text-button" data-go="data">저장 데이터 보기</button></div><div id="recent-runs"></div></article>
        </section>

        <section id="cameras" className="view">
          <div className="page-heading"><div><p className="eyebrow">LIVE CAMERA CHECK</p><h1>카메라 프리뷰</h1></div><div className="button-row"><button id="capture-all-cameras">모든 카메라 즉시 촬영</button><button id="refresh-previews" className="ghost">스트림 재연결</button></div></div>
          <p className="camera-help">카메라 상태를 실시간으로 확인합니다. 즉시 촬영한 사진은 현재 설정된 카메라 저장소에 저장됩니다.</p>
          <div id="camera-grid" className="camera-grid"></div>
        </section>

        <section id="sensors" className="view">
          <div className="page-heading"><div><p className="eyebrow">TEMPERATURE · HUMIDITY</p><h1>토양 센서 데이터</h1><p className="page-description">로컬 CSV 원본과 file/SFTP/FTPS 복제 상태를 함께 확인합니다.</p></div><div className="button-row"><button id="sync-sensor-storage" className="ghost">원격 재시도</button><button id="refresh-sensors">갱신</button></div></div>
          <div id="sensor-metric-grid" className="metric-grid"></div>
          <article className="panel sensor-data-panel"><div className="panel-title"><div><h2>온·습도 측정값</h2><span>CSV의 timestamp, temperature_c, humidity_percent</span></div></div><div id="sensor-data-table" className="empty">토양 센서 데이터를 불러오는 중입니다.</div></article>
          <article className="panel sensor-delivery-panel"><div className="panel-title"><div><h2>원격 동기화 상태</h2><span>로컬 CSV 전체 파일의 원자적 복제 결과</span></div></div><div id="sensor-delivery-table" className="empty">동기화 상태를 불러오는 중입니다.</div></article>
        </section>

        <section id="settings" className="view">
          <div className="page-heading"><div><p className="eyebrow">DEVICE SETTINGS</p><h1>설정</h1><p className="page-description">카메라 촬영·토양 센서 수집 시각과 저장 위치를 한곳에서 관리합니다.</p></div></div>

          <div className="settings-grid">
            <article className="panel settings-card capture-schedule-card camera-block">
              <div className="panel-title"><div><h2>카메라 촬영 일정</h2><span>카메라의 반복 촬영 시간과 간격을 설정합니다.</span></div></div>
              <form id="capture-schedule-form">
                <input id="capture-schedule-timezone" type="hidden" value="Asia/Seoul" />
                <div className="form-grid schedule-mode-grid">
                  <label className="field"><span>적용 대상</span><select id="capture-schedule-camera"></select></label>
                  <label className="field"><span>자동 촬영 방식</span><select id="capture-schedule-mode"><option value="disabled">사용 안 함</option><option value="interval">일정 간격마다</option><option value="daily">매일 지정한 시각</option></select></label>
                </div>
                <div className="capture-interval-field capture-duration-editor">
                  <div className="subsection-heading"><div><strong>촬영 간격</strong><span>일·시간·분·초 단위로 입력합니다.</span></div></div>
                  <div className="duration-grid">
                    <label className="field"><input id="capture-interval-days" type="number" min="0" max="9999" step="1" /><span>일</span></label>
                    <label className="field"><input id="capture-interval-hours" type="number" min="0" max="23" step="1" /><span>시간</span></label>
                    <label className="field"><input id="capture-interval-minutes" type="number" min="0" max="59" step="1" /><span>분</span></label>
                    <label className="field"><input id="capture-interval-seconds" type="number" min="0" max="59.99" step="0.01" /><span>초</span></label>
                  </div>
                </div>
                <div className="capture-daily-field">
                  <div className="subsection-heading"><div><strong>촬영 시간 블록</strong><span>기본값은 05:00부터 20:00까지 30분 간격이며 자유롭게 변경할 수 있습니다.</span></div><button id="add-capture-range" type="button" className="ghost">+ 블록 추가</button></div>
                  <div id="capture-range-list" className="capture-range-list"></div>
                  <button id="generate-daily-times" type="button" className="ghost generate-times-button">촬영 시각 생성</button>
                  <div className="subsection-heading generated-times-heading"><div><strong>생성된 촬영 시각</strong><span>초 단위 시각도 직접 조정할 수 있습니다.</span></div><div className="inline-actions"><button id="clear-daily-times" type="button" className="text-button danger-text">모두 지우기</button><button id="add-daily-time" type="button" className="ghost">+ 시각 추가</button></div></div>
                  <div id="daily-time-list" className="daily-time-list"></div>
                </div>
                <p id="capture-schedule-summary" className="field-help"></p>
                <p id="capture-schedule-warning" className="settings-warning" hidden>촬영 시각이 없습니다. 카메라 자동 촬영이 비활성화됩니다.</p>
                <div className="settings-actions storage-apply-actions"><span id="capture-schedule-note">촬영 일정을 불러오는 중입니다.</span><button id="apply-capture-schedule" type="submit">촬영 일정 적용</button></div>
              </form>
            </article>

            <article className="panel settings-card sensor-storage-card soil-sensor-block">
              <div className="panel-title"><div><h2>토양 센서 연결</h2><span>CWT 직렬 연결을 설정합니다. 측정값은 온도와 습도만 사용합니다.</span></div></div>
              <label className="field compact-field"><span>적용 대상</span><select id="sensor-settings-node"></select></label>
              <form id="sensor-settings-form">
                <div className="form-grid">
                  <label className="field"><span>드라이버</span><input id="sensor-driver" value="cwt-soil-thc-s" readOnly /></label>
                  <label className="field"><span>직렬 포트</span><input id="sensor-port" required placeholder="/dev/ttyAMA0" /></label>
                  <label className="field"><span>Baudrate</span><select id="sensor-baudrate"><option>1200</option><option>2400</option><option selected>4800</option><option>9600</option><option>19200</option><option>38400</option><option>57600</option><option>115200</option></select></label>
                  <label className="field"><span>Slave ID</span><input id="sensor-slave-id" type="number" min="1" max="254" step="1" required /></label>
                  <label className="field"><span>응답 제한(초)</span><input id="sensor-timeout" type="number" min="0.1" max="30" step="0.1" required /></label>
                </div>
                <div className="settings-actions storage-apply-actions"><span id="sensor-settings-note">토양 센서 설정을 불러오는 중입니다.</span><button id="apply-sensor-settings" type="submit">연결 설정 저장</button></div>
              </form>
            </article>

            <article className="panel settings-card capture-schedule-card soil-sensor-block">
              <div className="panel-title"><div><h2>토양 센서 수집 일정</h2><span>저장된 시각마다 온도와 습도를 읽고 전원 계획에 반영합니다.</span></div></div>
              <form id="sensor-schedule-form">
                <input id="sensor-schedule-timezone" type="hidden" value="Asia/Seoul" />
                <div className="form-grid schedule-mode-grid">
                  <label className="field"><span>적용 대상</span><select id="sensor-schedule-node"></select></label>
                  <label className="field"><span>자동 수집 방식</span><select id="sensor-schedule-mode"><option value="disabled">사용 안 함</option><option value="daily">매일 지정한 시각</option></select></label>
                </div>
                <div className="capture-daily-field sensor-daily-field">
                  <div className="subsection-heading"><div><strong>수집 시간 블록</strong><span>기본값은 00:00부터 23:30까지 30분 간격이며 자유롭게 변경할 수 있습니다.</span></div><button id="add-sensor-range" type="button" className="ghost">+ 블록 추가</button></div>
                  <div id="sensor-range-list" className="capture-range-list"></div>
                  <button id="generate-sensor-times" type="button" className="ghost generate-times-button">수집 시각 생성</button>
                  <div className="subsection-heading generated-times-heading"><div><strong>저장될 수집 시각</strong><span>실제 측정은 아래 시각 목록을 기준으로 실행됩니다.</span></div><div className="inline-actions"><button id="clear-sensor-times" type="button" className="text-button danger-text">모두 지우기</button><button id="add-sensor-time" type="button" className="ghost">+ 시각 추가</button></div></div>
                  <div id="sensor-time-list" className="daily-time-list"></div>
                </div>
                <p id="sensor-schedule-summary" className="field-help"></p>
                <p id="sensor-schedule-warning" className="settings-warning" hidden>수집 시각이 없습니다. 토양 센서 자동 수집이 비활성화됩니다.</p>
                <div className="settings-actions storage-apply-actions"><span id="sensor-schedule-note">토양 센서 일정을 불러오는 중입니다.</span><button id="apply-sensor-schedule" type="submit">수집 일정 적용</button></div>
              </form>
            </article>

            <article className="panel settings-card power-settings-card power-block">
              <div className="panel-title"><div><h2>전원 관리</h2><span>카메라 촬영·토양 센서 수집 일정에 따른 여유 시간과 별도 전원 유지 일정을 관리합니다.</span></div></div>
              <form id="power-settings-form">
                <p className="settings-warning power-schedule-warning">그 어떠한 일정도 설정되지 않으면 전원 관리가 비활성화됩니다.</p>
                <section className="power-settings-section">
                  <div className="subsection-heading"><div><strong>작동 전후 여유 시간</strong><span>카메라 촬영이나 토양 센서 수집 전에 전원을 미리 켜고 작업 후에 유지할 시간을 설정합니다.</span></div></div>
                  <div className="form-grid">
                    <label className="field"><span>작동 전 여유 시간(초)</span><input id="power-warmup-seconds" type="number" min="0" max="86400" step="1" required /></label>
                    <label className="field"><span>작동 후 여유 시간(초)</span><input id="power-cleanup-seconds" type="number" min="0" max="86400" step="1" required /></label>
                  </div>
                  <p className="field-help">작동 전 여유 시간은 카메라 촬영이나 토양 센서 수집 전에 전원을 미리 켜 두는 시간이며, 작동 후 여유 시간은 마지막 작업 후 전원을 유지하는 시간입니다.</p>
                </section>
                <section className="power-settings-section power-keep-section">
                  <div className="subsection-heading power-keep-heading"><div><strong>전원 유지 시간</strong><span>기본값은 매일 08:50~09:30, 17:20~18:10이며 자유롭게 변경할 수 있습니다.</span></div><button id="add-power-keep" type="button" className="ghost">+ 전원 유지 추가</button></div>
                  <div id="power-keep-list" className="power-keep-list"></div>
                </section>
                <div className="settings-actions storage-apply-actions"><span id="power-settings-note">전원 유지 일정은 촬영 일정과 독립적으로 매일 작동합니다.</span><button type="submit">전원 설정 적용</button></div>
              </form>
            </article>

            <article className="panel settings-card storage-card storage-block">
              <div className="panel-title"><div><h2>저장소</h2><span>카메라 촬영 파일과 토양 센서 CSV 원본·복제 위치를 한곳에서 관리합니다.</span></div></div>
              <section className="storage-section camera-storage-section">
              <div className="subsection-heading"><div><strong>카메라 촬영 파일</strong><span>촬영 파일을 저장할 위치를 카메라별로 지정합니다.</span></div><button id="add-storage" className="ghost">+ 저장소 추가</button></div>
              <label className="field compact-field"><span>적용 대상</span><select id="storage-camera"></select></label>
              <div id="storage-list" className="storage-list empty">카메라 설정을 불러오는 중입니다.</div>

              <form id="storage-editor" className="storage-editor" hidden>
                <input id="storage-edit-index" type="hidden" value="-1" />
                <div className="form-grid">
                  <label className="field"><span>저장 방식</span><select id="storage-type"><option value="file">이 장치의 폴더</option><option value="sftp">SFTP 서버</option><option value="ftps">FTPS 서버</option></select></label>
                  <label className="field"><span>표시 이름</span><input id="storage-name" required placeholder="예: local 또는 archive" /></label>
                  <label className="field"><span>폴더 구성</span><select id="storage-layout"><option value="daily">날짜별 폴더</option><option value="flat">한 폴더에 저장</option></select></label>
                  <label className="field storage-file-field"><span>저장 폴더</span><input id="storage-directory" placeholder="예: captures/camera0" /></label>
                  <label className="field storage-file-field checkbox-field"><input id="storage-retention-enabled" type="checkbox" /><span>기간이 지난 파일 자동 삭제</span></label>
                  <label className="field storage-file-field"><span>보존 기간(일)</span><input id="storage-retention-days" type="number" min="1" max="36500" step="1" value="7" /></label>
                  <label className="field storage-remote-field"><span>서버 주소</span><input id="storage-host" placeholder="storage.example.local" /></label>
                  <label className="field storage-remote-field"><span>포트</span><input id="storage-port" type="number" min="1" max="65535" /></label>
                  <label className="field storage-remote-field"><span>사용자 이름</span><input id="storage-username" /></label>
                  <label className="field storage-remote-field"><span>원격 폴더</span><input id="storage-remote-directory" placeholder="/captures/camera0" /></label>
                  <label className="field storage-sftp-field"><span>SSH 개인 키 경로</span><input id="storage-private-key" placeholder="~/.ssh/id_ed25519" /></label>
                  <label className="field storage-secret-field"><span>비밀번호 환경변수 이름</span><input id="storage-password-env" placeholder="CAMERA_STORAGE_PASSWORD" /></label>
                  <label className="field storage-ftps-field checkbox-field"><input id="storage-verify-tls" type="checkbox" checked /><span>TLS 인증서 검증</span></label>
                </div>
                <p className="field-help storage-file-field">카메라 런타임이 하루에 한 번 확인하여 보존 기간이 지난 파일을 자동으로 삭제합니다.</p>
                <p className="field-help storage-secret-field">비밀번호 값은 화면이나 JSON에 저장하지 않습니다. 지정한 환경변수는 장치의 보호된 환경 파일에서 제공해야 합니다.</p>
                <div className="settings-actions"><button id="cancel-storage" type="button" className="ghost">취소</button><button type="submit">목록에 저장</button></div>
              </form>
              <div className="settings-actions storage-apply-actions"><span id="storage-restart-note">저장소를 적용하면 카메라 런타임이 안전하게 재시작됩니다.</span><button id="apply-storages">저장소 적용</button></div>
              </section>

              <section className="storage-section sensor-csv-storage-section">
                <div className="subsection-heading sensor-mirror-heading"><div><strong>토양 센서 CSV</strong><span>로컬 CSV를 원본으로 보존하고 동일 상대 경로로 전체 파일을 복제합니다.</span></div><button id="add-sensor-mirror" type="button" className="ghost">+ 복제 대상 추가</button></div>
                <label className="field compact-field"><span>적용 대상</span><select id="sensor-storage-node"></select></label>
                <div className="form-grid">
                  <label className="field"><span>로컬 CSV 원본 폴더</span><input id="sensor-local-directory" required placeholder="/var/lib/pi-node-supervisor/sensors" /></label>
                  <label className="field"><span>재시도 시작(초)</span><input id="sensor-retry-base" type="number" min="1" max="3600" step="1" required /></label>
                  <label className="field"><span>재시도 최대(초)</span><input id="sensor-retry-max" type="number" min="1" max="86400" step="1" required /></label>
                </div>
                <div id="sensor-mirror-list" className="storage-list empty"></div>
                <div id="sensor-mirror-editor" className="storage-editor" hidden>
                  <input id="sensor-mirror-edit-index" type="hidden" value="-1" />
                  <div className="form-grid">
                    <label className="field"><span>방식</span><select id="sensor-mirror-type"><option value="file">이 장치의 폴더</option><option value="sftp">SFTP 서버</option><option value="ftps">FTPS 서버</option></select></label>
                    <label className="field"><span>표시 이름</span><input id="sensor-mirror-name" placeholder="예: backup" /></label>
                    <label className="field sensor-mirror-file-field"><span>복제 폴더</span><input id="sensor-mirror-directory" placeholder="/mnt/sensor-backup" /></label>
                    <label className="field sensor-mirror-remote-field"><span>서버 주소</span><input id="sensor-mirror-host" /></label>
                    <label className="field sensor-mirror-remote-field"><span>포트</span><input id="sensor-mirror-port" type="number" min="1" max="65535" /></label>
                    <label className="field sensor-mirror-remote-field"><span>사용자 이름</span><input id="sensor-mirror-username" /></label>
                    <label className="field sensor-mirror-remote-field"><span>원격 폴더</span><input id="sensor-mirror-remote-directory" placeholder="/sensors" /></label>
                    <label className="field sensor-mirror-sftp-field"><span>SSH 개인 키 경로</span><input id="sensor-mirror-private-key" /></label>
                    <label className="field sensor-mirror-sftp-field"><span>known_hosts 경로</span><input id="sensor-mirror-known-hosts" placeholder="~/.ssh/known_hosts" /></label>
                    <label className="field sensor-mirror-secret-field"><span>비밀번호 환경변수 이름</span><input id="sensor-mirror-password-env" placeholder="SENSOR_STORAGE_PASSWORD" /></label>
                    <label className="field sensor-mirror-ftps-field checkbox-field"><input id="sensor-mirror-verify-tls" type="checkbox" checked /><span>TLS 인증서 검증</span></label>
                  </div>
                  <p className="field-help">비밀번호 값은 브라우저나 nodes.json에 저장하지 않고 환경변수 이름만 저장합니다.</p>
                  <div className="settings-actions"><button id="cancel-sensor-mirror" type="button" className="ghost">취소</button><button id="save-sensor-mirror" type="button">목록에 저장</button></div>
                </div>
                <div className="settings-actions storage-apply-actions"><span id="sensor-storage-note">토양 센서 CSV 설정을 불러오는 중입니다.</span><button id="apply-sensor-storage-settings" type="button">CSV 저장소 적용</button></div>
              </section>
            </article>

            <details className="panel advanced-settings">
              <summary>고급 설정 · JSON 직접 편집</summary>
              <p className="help">토양 센서 업무나 유지보수 창처럼 일반 화면에 없는 기능이 필요할 때만 사용하세요.</p>
              <div className="button-row"><button id="load-draft" className="ghost">저장된 초안</button><button id="load-capture-node-settings" className="ghost">현재 카메라 설정 가져오기</button><button id="preview-plan" className="ghost">JSON 검증</button><button id="save-draft" className="ghost">초안 저장</button><button id="apply-draft" className="danger">JSON 적용</button></div>
              <textarea id="config-editor" spellCheck="false" aria-label="고급 업무 설정 JSON"></textarea>
            </details>
          </div>
        </section>

        <section id="events" className="view"><div className="page-heading"><div><p className="eyebrow">AUDIT LOG</p><h1>이벤트</h1><p className="page-description">카메라 재시작, 설정 변경, 오류처럼 장치에서 일어난 일을 시간순으로 보여줍니다.</p></div></div><article className="panel events-panel"><div id="events-table"></div><div id="events-pagination" className="pagination"><label>페이지당 <select id="events-page-size"><option value="25">25건</option><option value="50">50건</option><option value="100">100건</option></select></label><span id="events-page-info">0건</span><div className="pagination-actions"><button id="events-prev" type="button" className="ghost">이전</button><button id="events-next" type="button" className="ghost">다음</button></div></div></article></section>
        <section id="data" className="view"><div className="page-heading"><div><p className="eyebrow">DATA CATALOG</p><h1>수집 데이터</h1><p className="page-description">카메라 촬영 파일과 토양 센서 측정 기록을 자동으로 반영합니다.</p></div><button id="sync-data" className="ghost">데이터 갱신</button></div><article className="panel data-panel"><div id="data-table"></div><div id="data-pagination" className="pagination"><label>페이지당 <select id="data-page-size"><option value="25">25건</option><option value="50">50건</option><option value="100">100건</option></select></label><span id="data-page-info">0건</span><div className="pagination-actions"><button id="data-prev" type="button" className="ghost">이전</button><button id="data-next" type="button" className="ghost">다음</button></div></div></article></section>
        <section id="nodes" className="view"><div className="page-heading"><div><p className="eyebrow">MANAGED NODES</p><h1>연결된 장치</h1><p className="page-description">Supervisor가 관리하는 카메라와 실행 기능의 연결 상태입니다.</p></div></div><div id="node-grid" className="node-grid"></div></section>
      </main>
    </div>

      <dialog id="token-dialog">
      <form method="dialog">
        <p className="eyebrow">LOCAL AUTHENTICATION</p><h2>접속 토큰</h2>
        <p className="help">토큰은 이 브라우저의 sessionStorage에만 보관됩니다.</p>
        <input id="token-input" type="password" autoComplete="current-password" placeholder="X-Supervisor-Token" />
        <div className="dialog-actions"><button value="cancel" className="ghost">취소</button><button id="save-token" value="default">연결</button></div>
      </form>
    </dialog>
      <dialog id="data-view-dialog" className="data-view-dialog" aria-labelledby="data-view-title">
      <div className="data-view-heading">
        <strong id="data-view-title">원본 이미지</strong>
        <button id="close-data-view" type="button" className="ghost">닫기</button>
      </div>
      <div className="data-view-canvas">
        <img id="data-view-image" alt="저장된 원본 이미지" />
      </div>
    </dialog>
      <div id="toast" role="status" aria-live="polite"></div>
    </>
  );
}
