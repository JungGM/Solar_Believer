import React, { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { getJson } from "./api";
import "./styles.css";

const views = {
  observations: {
    title: "수집 데이터",
    columns: [
      ["source", "출처"],
      ["kind", "종류"],
      ["scope_id", "대상"],
      ["observed_at", "대상 시각"],
      ["values", "수집값"],
      ["is_demo", "데모 여부"],
    ],
  },
  predictions: {
    title: "발전량 예측",
    columns: [
      ["scope_id", "대상"],
      ["target_at", "예측 시각"],
      ["cutoff_at", "예측 기준 시각"],
      ["generation_kwh", "발전량 (kWh)"],
      ["model_version_id", "모델 ID"],
    ],
  },
  settlements: {
    title: "정산금 조회",
    columns: [
      ["prediction_id", "예측 ID"],
      ["rule_version", "규칙 버전"],
      ["amount_krw", "금액 (원)"],
      ["is_simulation", "시뮬레이션 여부"],
    ],
  },
  "model-versions": {
    title: "모델 버전",
    columns: [
      ["version", "버전"],
      ["status", "상태"],
      ["active", "적용 여부"],
      ["metrics", "검증 지표"],
      ["approved_by", "승인자"],
    ],
  },
  "training-runs": {
    title: "학습 이력",
    columns: [
      ["status", "상태"],
      ["cutoff_at", "학습 기준 시각"],
      ["metrics", "검증 지표"],
      ["note", "메모"],
    ],
  },
  "job-runs": {
    title: "작업 이력",
    columns: [
      ["job_type", "작업"],
      ["status", "상태"],
      ["row_count", "저장 건수"],
      ["started_at", "시작 시각"],
      ["message", "내용"],
    ],
  },
};
const labels = {
  completed: "완료",
  running: "진행 중",
  skipped: "연동 대기",
  failed: "실패",
  candidate: "후보",
  validated: "검증 완료",
  approved: "승인 완료",
  retired: "사용 종료",
};

function format(value, key) {
  if (value === null || value === undefined) return "—";
  if (typeof value === "boolean") return value ? "예" : "아니요";
  if (typeof value === "object") return JSON.stringify(value);
  if (key.endsWith("_at"))
    return new Date(value).toLocaleString("ko-KR", { timeZone: "Asia/Seoul" });
  return labels[value] ?? String(value);
}

function App() {
  const [view, setView] = useState("observations");
  const [summary, setSummary] = useState(null);
  const [rows, setRows] = useState([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [revision, refresh] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError("");
    Promise.all([
      getJson("summary", controller.signal),
      getJson(view, controller.signal),
    ])
      .then(([stats, records]) => {
        setSummary(stats);
        setRows(records);
      })
      .catch((err) => {
        if (err.name !== "AbortError") setError(err.message);
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [view, revision]);
  const selected = views[view];
  return (
    <div className="shell">
      <aside>
        <a className="brand" href="/">
          <span className="sun">☀</span>
          <span>
            Solar
            <br />
            Believer<span className="brand-small">ENERGY WORKSPACE</span>
          </span>
        </a>
        <div className="nav-caption">운영 대시보드</div>
        <nav aria-label="주 메뉴">
          {Object.entries(views).map(([key, info], index) => (
            <button
              key={key}
              className={view === key ? "selected" : ""}
              onClick={() => setView(key)}
            >
              <span className="nav-number">0{index + 1}</span>
              {info.title}
            </button>
          ))}
        </nav>
        <div className="aside-note">
          <span className="dot" /> 초기 개발 환경
          <p>
            공공데이터 기반
            <br />
            태양광 예측 · 정산 조회
          </p>
        </div>
      </aside>
      <main>
        <header>
          <div className="breadcrumb">
            Solar Believer <span>/</span> Workspace
          </div>
          <span className="phase">PHASE 01 · 기본 구조</span>
        </header>
        <section className="intro">
          <div>
            <div className="eyebrow">SOLAR OPERATIONS</div>
            <h1>
              에너지 데이터,
              <br />
              한곳에서 확인하세요.
            </h1>
            <p>수집부터 예측, 모델 관리까지 연결하는 팀의 작업 공간입니다.</p>
          </div>
          <div className="intro-icon" aria-hidden="true">
            ↗
          </div>
        </section>
        <div className="notice">
          <span>안내</span> 실제 API·모델·정산 규칙 연동을 준비 중입니다. 데모
          데이터는 별도로 표시하며, 정산 규칙 확정 전 금액은 제공하지 않습니다.
        </div>
        <section className="stats" aria-label="서비스 현황">
          {[
            [
              "수집 데이터",
              summary?.observations,
              "건",
              `데모 ${summary?.demo_observations ?? "—"}건 포함`,
            ],
            ["발전량 예측", summary?.predictions, "건", "승인 모델 기준"],
            ["정산 기록", summary?.settlements, "건", "정산 규칙 연동 예정"],
            [
              "적용 모델",
              summary?.active_model ?? "미등록",
              "",
              "검증 → 승인 → 적용",
            ],
          ].map(([title, value, unit, note]) => (
            <article className="stat" key={title}>
              <div>{title}</div>
              <strong>
                {value ?? "—"}
                <small>{unit}</small>
              </strong>
              <p>{note}</p>
            </article>
          ))}
        </section>
        <section className="records">
          <div className="section-heading">
            <div>
              <div className="eyebrow">DATA EXPLORER</div>
              <h2>{selected.title}</h2>
            </div>
            <button
              className="refresh"
              disabled={loading}
              onClick={() => refresh(revision + 1)}
            >
              ↻ 새로고침
            </button>
          </div>
          <p className="table-caption">
            최근 100건 · 표시 시각 KST · 수집 모드{" "}
            {summary?.pipeline_mode ?? "확인 중"}
          </p>
          {error ? (
            <div role="alert" className="error">
              {error}. 서비스 상태를 확인한 뒤 새로고침해 주세요.
            </div>
          ) : loading ? (
            <div className="empty" role="status">
              데이터를 불러오는 중입니다.
            </div>
          ) : rows.length === 0 ? (
            <div className="empty">
              <span className="empty-icon">☷</span>
              <h3>아직 등록된 기록이 없습니다</h3>
              <p>수집 또는 작업 실행 후 이곳에서 확인할 수 있습니다.</p>
            </div>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    {selected.columns.map(([key, title]) => (
                      <th key={key}>{title}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row) => (
                    <tr key={row.id}>
                      {selected.columns.map(([key]) => (
                        <td key={key}>
                          {key === "status" ? (
                            <span className={`status ${row[key]}`}>
                              {format(row[key], key)}
                            </span>
                          ) : (
                            format(row[key], key)
                          )}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
        <footer>
          <span>SOLAR BELIEVER</span>
          <span>팀 개발용 기본 환경 · v0.1.0</span>
        </footer>
      </main>
    </div>
  );
}

createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
