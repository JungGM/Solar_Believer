import React, { useEffect, useState } from "react";
import { getJson } from "../api";

export const recordViews = {
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
    title: "예측 기록",
    columns: [
      ["scope_id", "대상"],
      ["target_at", "예측 시각"],
      ["cutoff_at", "기준 시각"],
      ["generation_kwh", "발전량 (kWh)"],
      ["model_version_id", "모델 ID"],
    ],
  },
  settlements: {
    title: "기존 정산 기록",
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
      ["metrics", "지표"],
      ["approved_by", "승인자"],
    ],
  },
  "training-runs": {
    title: "학습 이력",
    columns: [
      ["status", "상태"],
      ["cutoff_at", "학습 기준"],
      ["metrics", "지표"],
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
export default function RecordsPanel({ view }) {
  const [state, setState] = useState({ loading: true, rows: [], error: "" });
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setState({ loading: true, rows: [], error: "" });
    getJson(view, controller.signal)
      .then((rows) => setState({ loading: false, rows, error: "" }))
      .catch((err) => {
        if (err.name !== "AbortError")
          setState({ loading: false, rows: [], error: err.message });
      });
    return () => controller.abort();
  }, [view, revision]);
  const selected = recordViews[view];
  return (
    <>
      <div className="page-title">
        <div>
          <span className="eyebrow">DATA EXPLORER</span>
          <h1>{selected.title}</h1>
          <p>최근 100건 · 한국 시간(KST) 표시</p>
        </div>
        <button
          className="button secondary"
          onClick={() => setRevision(revision + 1)}
        >
          ↻ 새로고침
        </button>
      </div>
      <section className="panel records-panel">
        {state.error ? (
          <div role="alert" className="error">
            {state.error}
          </div>
        ) : state.loading ? (
          <div className="large-empty" role="status">
            조회 중입니다.
          </div>
        ) : !state.rows.length ? (
          <div className="large-empty">아직 등록된 기록이 없습니다.</div>
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
                {state.rows.map((row) => (
                  <tr key={row.id}>
                    {selected.columns.map(([key]) => (
                      <td key={key}>{format(row[key], key)}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </>
  );
}
