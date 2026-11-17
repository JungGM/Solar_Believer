import React, { useEffect, useState } from "react";
import { getJson } from "./api";
import KoreaMap from "./components/KoreaMap";
import EnergyChart from "./components/EnergyCharts";

const num = (value, digits = 1) =>
  value == null
    ? "—"
    : Number(value).toLocaleString("ko-KR", { maximumFractionDigits: digits });
const today = () =>
  new Intl.DateTimeFormat("sv-SE", { timeZone: "Asia/Seoul" }).format(
    new Date(),
  );
const units = { day: "일별", month: "월별", year: "연별" };
function validDate(value) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value || "")) return false;
  const year = Number(value.slice(0, 4));
  const parsed = new Date(`${value}T00:00:00Z`);
  return (
    year >= 2000 &&
    year <= 2100 &&
    !Number.isNaN(parsed.getTime()) &&
    parsed.toISOString().slice(0, 10) === value
  );
}
function initialFilters() {
  const q = new URLSearchParams(window.location.search);
  return {
    demo: q.get("demo") === "true",
    period: ["day", "month", "year"].includes(q.get("period"))
      ? q.get("period")
      : "day",
    date: validDate(q.get("date")) ? q.get("date") : today(),
    portfolio: q.get("portfolio_id") || "",
    model: q.get("model_version_id") || "",
    rule: q.get("settlement_rule_version") || "",
  };
}
function makeQuery(f) {
  const q = new URLSearchParams({
    demo: String(f.demo),
    period: f.period,
    date: f.date,
  });
  if (f.portfolio) q.set("portfolio_id", f.portfolio);
  if (f.model) q.set("model_version_id", f.model);
  if (f.rule) q.set("settlement_rule_version", f.rule);
  return q;
}
function shiftDate(f, delta) {
  const [year, month, day] = f.date.split("-").map(Number);
  const d = new Date(Date.UTC(year, month - 1, f.period === "day" ? day : 1));
  if (f.period === "day") d.setUTCDate(d.getUTCDate() + delta);
  else if (f.period === "month") d.setUTCMonth(d.getUTCMonth() + delta);
  else d.setUTCFullYear(d.getUTCFullYear() + delta);
  return d.toISOString().slice(0, 10);
}

export default function Dashboard() {
  const [f, setFilters] = useState(initialFilters);
  const [state, setState] = useState({
    data: null,
    options: null,
    loading: true,
    error: "",
    updated: null,
  });
  const [revision, refresh] = useState(0);
  const [moneyField, setMoneyField] = useState("settlement_krw");
  const set = (key, value) => setFilters((prev) => ({ ...prev, [key]: value }));
  useEffect(() => {
    const c = new AbortController();
    const query = makeQuery(f);
    window.history.replaceState({}, "", `${window.location.pathname}?${query}`);
    setState((prev) => ({ ...prev, data: null, loading: true, error: "" }));
    Promise.all([
      getJson(`dashboard/options?demo=${f.demo}`, c.signal),
      getJson(`dashboard?${query}`, c.signal),
    ])
      .then(([options, data]) =>
        setState({
          options,
          data,
          loading: false,
          error: "",
          updated: new Date(),
        }),
      )
      .catch((err) => {
        if (err.name !== "AbortError")
          setState((prev) => ({ ...prev, loading: false, error: err.message }));
      });
    return () => c.abort();
  }, [f, revision]);
  const { data, options, loading, error } = state;
  const groups = options?.portfolios || [];
  const model = options?.models.find((m) => m.id === data?.model_version_id);
  const s = data?.summary || {};
  const series = data?.series || [];
  const plants = data?.plants || [];
  const periodTitle =
    f.period === "day"
      ? f.date.replaceAll("-", ". ")
      : f.period === "month"
        ? `${f.date.slice(0, 4)}년 ${Number(f.date.slice(5, 7))}월`
        : `${f.date.slice(0, 4)}년`;
  const coverage = (count) =>
    s.expected_hours
      ? `${count ?? 0} / ${s.expected_hours}시간`
      : "비교 구간 대기";
  const toggleDemo = () =>
    setFilters((prev) => ({
      ...prev,
      demo: !prev.demo,
      portfolio: "",
      model: "",
      rule: "",
    }));
  return (
    <div className="dashboard">
      <div className="page-title">
        <div>
          <span className="eyebrow">VIRTUAL POWER PLANT</span>
          <h1>VPP 운영 현황</h1>
          <p>발전소의 위치부터 발전량 예측과 정산까지, 한눈에 확인하세요.</p>
        </div>
        <button
          className={`button ${f.demo ? "demo-active" : "secondary"}`}
          onClick={toggleDemo}
        >
          {f.demo ? "데모 종료 · 실제 데이터" : "데모 데이터 보기"}
        </button>
      </div>
      <div
        className={`mode-banner ${f.demo ? "demo-banner" : ""}`}
        role="status"
      >
        <span className="mode-label">{f.demo ? "DEMO" : "실제 데이터"}</span>
        <span>
          {f.demo
            ? "모든 지점·모델·발전량·SMP는 화면 확인용 가상 데이터입니다. 정산금은 생성하지 않습니다."
            : "등록된 발전소·VPP·모델의 저장 기록을 조회합니다. 실제 API와 모델 연결을 준비 중입니다."}
        </span>
      </div>
      <section className="filters" aria-label="대시보드 조회 조건">
        <label className="filter-field">
          <span>VPP 그룹</span>
          <select
            value={f.portfolio}
            onChange={(e) => set("portfolio", e.target.value)}
            aria-label="VPP 그룹"
            disabled={loading}
          >
            <option value="">전체 VPP 그룹</option>
            {groups.map((g) => (
              <option value={g.id} key={g.id}>
                {g.name} · {g.version}
              </option>
            ))}
          </select>
        </label>
        <label className="filter-field model-filter">
          <span>예측 모델</span>
          <select
            value={f.model || options?.models[0]?.id || ""}
            onChange={(e) => set("model", e.target.value)}
            aria-label="예측 모델"
            disabled={loading}
          >
            {!options?.models.length && (
              <option value="">등록된 모델 없음</option>
            )}
            {options?.models.map((m) => (
              <option key={m.id} value={m.id}>
                {m.version}
                {m.active
                  ? " · 적용 중"
                  : m.status === "candidate"
                    ? " · 후보"
                    : ""}
              </option>
            ))}
          </select>
        </label>
        <div className="filter-field period-filter">
          <span>조회 단위</span>
          <div className="segmented" role="group" aria-label="조회 단위">
            {Object.entries(units).map(([key, label]) => (
              <button
                key={key}
                aria-pressed={f.period === key}
                onClick={() => set("period", key)}
              >
                {label}
              </button>
            ))}
          </div>
        </div>
        <div className="filter-field date-filter">
          <label htmlFor="dashboard-date">조회 기간 · KST</label>
          <div className="date-control">
            <button
              aria-label="이전 기간"
              onClick={() => set("date", shiftDate(f, -1))}
              disabled={!validDate(shiftDate(f, -1))}
            >
              ‹
            </button>
            {f.period === "year" ? (
              <select
                id="dashboard-date"
                aria-label="조회 연도"
                value={f.date.slice(0, 4)}
                onChange={(e) => set("date", `${e.target.value}-01-01`)}
              >
                {Array.from({ length: 101 }, (_, i) => (
                  <option key={i}>{2000 + i}</option>
                ))}
              </select>
            ) : (
              <input
                id="dashboard-date"
                aria-label="조회 기간"
                type={f.period === "day" ? "date" : "month"}
                min={f.period === "day" ? "2000-01-01" : "2000-01"}
                max={f.period === "day" ? "2100-12-31" : "2100-12"}
                value={f.period === "day" ? f.date : f.date.slice(0, 7)}
                onChange={(e) => {
                  if (e.target.value)
                    set(
                      "date",
                      f.period === "day"
                        ? e.target.value
                        : `${e.target.value}-01`,
                    );
                }}
              />
            )}
            <button
              aria-label="다음 기간"
              onClick={() => set("date", shiftDate(f, 1))}
              disabled={!validDate(shiftDate(f, 1))}
            >
              ›
            </button>
          </div>
        </div>
        <button
          className="button refresh-button"
          aria-label="대시보드 새로고침"
          onClick={() => refresh(revision + 1)}
          disabled={loading}
        >
          ↻
        </button>
      </section>
      {error && (
        <div className="error" role="alert">
          {error}. API 상태 또는 선택 조건을 확인해 주세요.{" "}
          <button
            className="text-button"
            onClick={() =>
              setFilters({
                ...initialFilters(),
                portfolio: "",
                model: "",
                rule: "",
                date: today(),
              })
            }
          >
            조건 초기화
          </button>
        </div>
      )}
      {!loading && !error && !groups.length && (
        <div className="empty-notice">
          등록된 VPP가 없습니다. 발전소와 그룹을 연결하면 지도와 그래프가
          표시됩니다.{" "}
          <button className="text-button" onClick={toggleDemo}>
            데모로 화면 살펴보기 →
          </button>
        </div>
      )}
      <div
        className={`dashboard-results ${loading || error ? "is-pending" : ""}`}
        aria-busy={loading}
      >
        <div className="results-title">
          <h2>
            {periodTitle}
            <span>
              {f.portfolio
                ? groups.find((g) => String(g.id) === f.portfolio)?.name ||
                  "선택 그룹"
                : "전체 포트폴리오"}
            </span>
          </h2>
          <span>
            {loading
              ? "조회 중…"
              : state.updated
                ? `화면 조회 ${state.updated.toLocaleTimeString("ko-KR", { timeZone: "Asia/Seoul", hour: "2-digit", minute: "2-digit" })}`
                : "조회 대기"}
          </span>
        </div>
        <section className="kpis" aria-label="선택 조건 요약">
          <Metric
            title="예측 발전량"
            value={s.predicted_kwh == null ? null : s.predicted_kwh / 1000}
            unit="MWh"
            note={`값이 있는 전체 발전소 구간 · ${coverage(s.predicted_hours)}`}
            accent="gold"
          />
          <Metric
            title="실제 발전량"
            value={s.actual_kwh == null ? null : s.actual_kwh / 1000}
            unit="MWh"
            note={`값이 있는 전체 발전소 구간 · ${coverage(s.actual_hours)}`}
            accent="green"
          />
          <Metric
            title="시간별 평균 절대오차"
            value={s.mae_kwh == null ? null : s.mae_kwh / 1000}
            unit="MWh"
            note={`실측·예측 일치 구간 · ${coverage(s.paired_hours)}`}
            accent="neutral"
          />
          <Metric
            title={
              data?.settlement.is_simulation
                ? "시뮬레이션 정산금"
                : "저장된 정산금"
            }
            value={s.settlement_krw}
            unit="원"
            note={
              data?.settlement.rule_version
                ? `규칙 ${data.settlement.rule_version} · 저장된 구간 합계`
                : "정산 규칙 연결 대기"
            }
            accent="neutral"
            digits={0}
          />
        </section>
        <div className="overview-grid">
          <KoreaMap
            plants={plants}
            groups={groups}
            selectedGroup={f.portfolio}
            onGroupChange={(id) => set("portfolio", id)}
            isDemo={f.demo}
          />
          <section className="panel generation-panel">
            <div className="panel-heading">
              <div>
                <span className="eyebrow">GENERATION</span>
                <h2>예측량과 실제 발전량</h2>
              </div>
              <span className="quiet-badge">{units[f.period]}</span>
            </div>
            <EnergyChart series={series} period={f.period} />
            <div className="model-note">
              <span className="dot" />
              {model?.version || "모델 미등록"}
              <span>
                {data?.prediction_policy || "대상 시각까지 생성된 예측 조회"}
              </span>
            </div>
          </section>
        </div>
        <div className="secondary-grid">
          <section className="panel">
            <div className="panel-heading">
              <div>
                <span className="eyebrow">MARKET PRICE</span>
                <h2>SMP 추이</h2>
              </div>
              <span className="chart-head-value">
                {s.smp_land != null && s.smp_jeju != null
                  ? "육지 · 제주"
                  : num(s.smp_land ?? s.smp_jeju)}
                <small>
                  {s.smp_land != null && s.smp_jeju != null
                    ? `${num(s.smp_land)} / ${num(s.smp_jeju)} 원/kWh`
                    : `원/kWh · ${s.smp_jeju != null ? "제주" : "육지"} 시간 평균`}
                </small>
              </span>
            </div>
            <EnergyChart kind="price" series={series} period={f.period} />
          </section>
          <section className="panel">
            <div className="panel-heading">
              <div>
                <span className="eyebrow">FORECAST ERROR</span>
                <h2>발전량 예측오차</h2>
              </div>
              <span className="quiet-badge">금액 환산 전</span>
            </div>
            <EnergyChart kind="error" series={series} period={f.period} />
          </section>
        </div>
        <section className="panel money-panel">
          <div className="panel-heading">
            <div>
              <span className="eyebrow">SETTLEMENT</span>
              <h2>정산 결과</h2>
            </div>
            <div className="money-controls">
              <label>
                규칙
                <select
                  aria-label="정산 규칙"
                  disabled={!options?.settlement_rules.length}
                  value={
                    f.rule ||
                    (options?.settlement_rules.length === 1
                      ? options.settlement_rules[0]
                      : "")
                  }
                  onChange={(e) => set("rule", e.target.value)}
                >
                  <option value="">
                    {options?.settlement_rules.length
                      ? "규칙 버전 선택"
                      : "미설정"}
                  </option>
                  {options?.settlement_rules.map((rule) => (
                    <option key={rule}>{rule}</option>
                  ))}
                </select>
              </label>
              <div className="segmented" role="group" aria-label="금액 지표">
                <button
                  aria-pressed={moneyField === "settlement_krw"}
                  onClick={() => setMoneyField("settlement_krw")}
                >
                  정산금
                </button>
                <button
                  aria-pressed={moneyField === "loss_krw"}
                  onClick={() => setMoneyField("loss_krw")}
                >
                  금전적 손실
                </button>
              </div>
            </div>
          </div>
          {data?.settlement.status === "unconfigured" ? (
            <div className="settlement-empty">
              <span className="empty-symbol" aria-hidden="true">
                ₩
              </span>
              <div>
                <strong>
                  {options?.settlement_rules.length
                    ? "조회할 정산 규칙을 선택해 주세요"
                    : "정산 규칙 연결을 기다리고 있습니다"}
                </strong>
                <p>
                  정산금과 금전적 손실은 규칙별 계산 결과가 저장된 후
                  표시됩니다. 발전량 오차를 금액으로 임의 환산하지 않습니다.
                </p>
              </div>
            </div>
          ) : (
            <EnergyChart
              kind="money"
              moneyField={moneyField}
              series={series}
              period={f.period}
            />
          )}
        </section>
        <div className="detail-grid">
          <section className="panel plant-list">
            <div className="panel-heading">
              <div>
                <span className="eyebrow">PORTFOLIO ASSETS</span>
                <h2>구성 발전소</h2>
              </div>
              <span className="quiet-badge">
                {s.plant_count ?? 0}개 · {num((s.capacity_kw || 0) / 1000)} MW
                참여
              </span>
            </div>
            {plants.length ? (
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>발전소 / VPP</th>
                      <th>설비용량</th>
                      <th>예측 / 실측</th>
                      <th>실측 확보</th>
                    </tr>
                  </thead>
                  <tbody>
                    {plants.map((plant) => (
                      <tr key={plant.id}>
                        <td>
                          <strong>{plant.name}</strong>
                          <span className="cell-subtitle">
                            {plant.portfolio_ids
                              .map(
                                (id) =>
                                  groups.find((g) => g.id === id)?.name || id,
                              )
                              .join(", ")}
                          </span>
                        </td>
                        <td>{num(plant.capacity_kw / 1000)} MW</td>
                        <td>
                          {num(
                            plant.predicted_kwh == null
                              ? null
                              : plant.predicted_kwh / 1000,
                          )}{" "}
                          /{" "}
                          {num(
                            plant.actual_kwh == null
                              ? null
                              : plant.actual_kwh / 1000,
                          )}
                          <span className="cell-subtitle">
                            MWh · 알려진 구간 합계
                          </span>
                        </td>
                        <td>
                          {plant.actual_hours}/{plant.expected_hours}h
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <div className="large-empty">VPP에 등록된 발전소가 없습니다.</div>
            )}
          </section>
          <section className="panel quality-panel">
            <div className="panel-heading">
              <div>
                <span className="eyebrow">DATA QUALITY</span>
                <h2>데이터 확인</h2>
              </div>
            </div>
            <Coverage
              label="실측 확보"
              count={s.actual_hours}
              expected={s.expected_hours}
            />
            <Coverage
              label="예측 확보"
              count={s.predicted_hours}
              expected={s.expected_hours}
            />
            <Coverage
              label="오차 비교 가능"
              count={s.paired_hours}
              expected={s.expected_hours}
            />
            <p className="quality-note">
              전체 참여 발전소에 값이 있는 시간 기준입니다. 예측·실측 요약의
              대상 구간이 다르면 총량을 직접 비교하지 마세요.
            </p>
            {data?.warnings.map((warning) => (
              <p className="quality-warning" key={warning}>
                {warning}
              </p>
            ))}
          </section>
        </div>
      </div>
    </div>
  );
}
function Metric({ title, value, unit, note, accent, digits = 1 }) {
  return (
    <article className={`metric metric-${accent}`}>
      <div className="metric-title">
        <span className="metric-dot" />
        {title}
      </div>
      <strong>
        {num(value, digits)}
        <small>{unit}</small>
      </strong>
      <p>{note}</p>
    </article>
  );
}
function Coverage({ label, count = 0, expected = 0 }) {
  const percent = expected ? Math.round((count / expected) * 100) : null;
  return (
    <div className="coverage">
      <div>
        <span>{label}</span>
        <strong>{percent === null ? "—" : `${percent}%`}</strong>
      </div>
      <progress aria-label={label} max="100" value={percent || 0} />
      <small>
        {expected ? `${count} / ${expected}시간` : "대상 구간 미등록"}
      </small>
    </div>
  );
}
