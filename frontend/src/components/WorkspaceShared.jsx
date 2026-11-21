import React, { useEffect, useState } from "react";
import { getJson } from "../api";

export const fmt = (v, digits = 1) =>
  v == null
    ? "—"
    : Number(v).toLocaleString("ko-KR", { maximumFractionDigits: digits });
export const day = (offset = 0) => {
  const today = new Intl.DateTimeFormat("sv-SE", {
    timeZone: "Asia/Seoul",
  }).format(new Date());
  const date = new Date(`${today}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() + offset);
  return date.toISOString().slice(0, 10);
};
export const time = (v) =>
  v
    ? new Date(v).toLocaleString("ko-KR", {
        timeZone: "Asia/Seoul",
        hour12: false,
      })
    : "미확인";
export const dateLabel = (v) =>
  v
    ? new Date(v).toLocaleDateString("ko-KR", { timeZone: "Asia/Seoul" })
    : "미확인";
export function moneyNote(data) {
  if (!data?.settlement?.rule_version) return "정산 엔진·규칙 연결 대기";
  return `${data.settlement.is_simulation ? "시뮬레이션 · " : ""}저장된 구간 합계 · 지급 확정 아님`;
}
export function useResource(url, revision) {
  const [state, set] = useState({
    url: null,
    loading: true,
    data: null,
    error: "",
  });
  useEffect(() => {
    if (!url) return;
    const controller = new AbortController();
    set({ url, loading: true, data: null, error: "" });
    getJson(url, controller.signal)
      .then((data) => set({ url, data, loading: false, error: "" }))
      .catch((err) => {
        if (err.name !== "AbortError")
          set({ url, data: null, loading: false, error: err.message });
      });
    return () => controller.abort();
  }, [url, revision]);
  return state.url === url && url
    ? state
    : { loading: Boolean(url), data: null, error: "" };
}
export function Panel({ title, eyebrow, action, children, className = "" }) {
  return (
    <section className={`panel ${className}`}>
      <div className="panel-heading">
        <div>
          {eyebrow && <span className="eyebrow">{eyebrow}</span>}
          <h2>{title}</h2>
        </div>
        {action}
      </div>
      {children}
    </section>
  );
}
export function Empty({ title, children }) {
  return (
    <div className="workspace-empty">
      <span aria-hidden="true">⌁</span>
      <strong>{title}</strong>
      {children && <p>{children}</p>}
    </div>
  );
}
export function ResourceState({ resource, children }) {
  if (resource.error)
    return (
      <div className="error" role="alert">
        {resource.error} · API와 DB 연결 상태를 확인한 후 새로고침해 주세요.
      </div>
    );
  if (resource.loading)
    return (
      <div className="workspace-loading" role="status">
        기록을 조회하고 있습니다…
      </div>
    );
  return children;
}
export function Stat({ label, value, unit, note, accent = "neutral" }) {
  return (
    <article className={`metric metric-${accent}`}>
      <div className="metric-title">{label}</div>
      <strong>
        {value}
        <small>{unit}</small>
      </strong>
      <p>{note}</p>
    </article>
  );
}
export function Warnings({ data }) {
  return (
    <>
      {data?.warnings?.map((w) => (
        <p className="quality-warning" key={w}>
          {w}
        </p>
      ))}
    </>
  );
}
export function PlantTable({ plants = [], groups = [], forecastOnly = false }) {
  if (!plants.length)
    return (
      <Empty title="조회할 발전기가 없습니다">
        발전기와 VPP 구성원을 등록하면 표시됩니다.
      </Empty>
    );
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>발전기 / VPP</th>
            <th>설비용량</th>
            <th>예측 발전량</th>
            {!forecastOnly && <th>실제 발전량</th>}
            <th>예측 확보</th>
          </tr>
        </thead>
        <tbody>
          {plants.map((p) => (
            <tr key={p.id}>
              <td>
                <strong>{p.name}</strong>
                <span className="cell-subtitle">
                  {p.portfolio_ids
                    .map((id) => groups.find((g) => g.id === id)?.name || id)
                    .join(" · ")}
                </span>
              </td>
              <td>{fmt(p.capacity_kw / 1000)} MW</td>
              <td>
                {fmt(p.predicted_kwh == null ? null : p.predicted_kwh / 1000)}{" "}
                MWh
              </td>
              {!forecastOnly && (
                <td>
                  {fmt(p.actual_kwh == null ? null : p.actual_kwh / 1000)} MWh
                </td>
              )}
              <td>
                {p.predicted_hours}/{p.expected_hours}시간
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
