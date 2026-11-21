import React, { useState } from "react";
import EnergyChart from "./components/EnergyCharts";
import KoreaMap from "./components/KoreaMap";
import RecordsPanel, { recordViews } from "./components/RecordsPanel";
import { Assets, Weather, PortfolioHistory } from "./components/AssetWeather";
import {
  useResource,
  Panel,
  Empty,
  ResourceState,
  Stat,
  PlantTable,
  Warnings,
  fmt,
  day,
  dateLabel,
  moneyNote,
} from "./components/WorkspaceShared";

export const pages = [
  {
    id: "home",
    label: "홈 · 운영 현황",
    icon: "◫",
    description: "내일의 발전 계획과 오늘의 운영 결과를 확인하세요.",
  },
  {
    id: "forecast",
    label: "내일 발전량 예측",
    icon: "☀",
    description: "발전기와 VPP별로 다음 날 시간대별 발전량을 확인하세요.",
  },
  {
    id: "results",
    label: "발전 실적 · 정산",
    icon: "▥",
    description: "하루 전 예측과 실제 발전량, 저장된 정산 결과를 확인하세요.",
  },
  {
    id: "plants",
    label: "발전기 지도 · 이력",
    icon: "◎",
    description: "발전기의 위치와 기본 정보, 데이터 관측 이력을 확인하세요.",
  },
  {
    id: "weather",
    label: "날씨 · 예보 비교",
    icon: "☁",
    description: "예보 발표분과 실제 기상 관측을 지점별로 살펴보세요.",
  },
  {
    id: "portfolios",
    label: "VPP 구성 · 분석",
    icon: "▦",
    description: "그룹의 구성과 적용 기간, 합산 예측오차를 확인하세요.",
  },
  {
    id: "manage",
    label: "데이터 · 모델 관리",
    icon: "⚙",
    description: "수집 상태와 모델 성능, 학습·적용 기록을 확인하세요.",
  },
];
const units = { day: "일별", month: "월별", year: "연별" };

export default function Workspace({
  page,
  demo,
  revision,
  navigate,
  onRefresh,
}) {
  const info = pages.find((p) => p.id === page);
  const supportsDemo = ["home", "forecast", "results", "portfolios"].includes(
    page,
  );
  const effectiveDemo = demo && supportsDemo;
  const [date, setDate] = useState(day);
  const [forecastDate, setForecastDate] = useState(() => day(1));
  const [period, setPeriod] = useState("day");
  const [portfolio, setPortfolio] = useState("");
  const [model, setModel] = useState("");
  const [rule, setRule] = useState("");
  const options = useResource(
    supportsDemo ? `dashboard/options?demo=${effectiveDemo}` : null,
    revision,
  );
  const selectedDate =
    page === "home" ? day() : page === "forecast" ? forecastDate : date;
  const q = new URLSearchParams({
    demo: String(effectiveDemo),
    date: selectedDate,
    period: ["results", "portfolios"].includes(page) ? period : "day",
    prediction_policy: "day_ahead",
  });
  if (portfolio) q.set("portfolio_id", portfolio);
  if (model) q.set("model_version_id", model);
  if (rule && page === "results") q.set("settlement_rule_version", rule);
  const data = useResource(supportsDemo ? `dashboard?${q}` : null, revision);
  const tomorrowQuery = new URLSearchParams(q);
  tomorrowQuery.set("date", day(1));
  const tomorrow = useResource(
    page === "home" ? `dashboard?${tomorrowQuery}` : null,
    revision,
  );
  const assets = useResource(
    ["plants", "portfolios"].includes(page) && !effectiveDemo
      ? "workspace/plants"
      : null,
    revision,
  );
  function toggleDemo() {
    setPortfolio("");
    setModel("");
    setRule("");
    navigate(page, !demo);
  }
  return (
    <div className="workspace-content">
      <div className="page-title">
        <div>
          <span className="eyebrow">SOLAR OPERATIONS</span>
          <h1>{info.label}</h1>
          <p>{info.description}</p>
        </div>
        <div className="page-actions">
          {supportsDemo && (
            <button
              className={`button ${demo ? "demo-active" : "secondary"}`}
              onClick={toggleDemo}
            >
              {demo ? "실제 데이터 보기" : "데모 살펴보기"}
            </button>
          )}
          <button className="button secondary" onClick={onRefresh}>
            새로고침
          </button>
        </div>
      </div>
      <div
        className={`mode-banner ${effectiveDemo ? "demo-banner" : ""}`}
        role="status"
      >
        <span className="mode-label">
          {effectiveDemo ? "DEMO" : "실제 기록"}
        </span>
        <span>
          {effectiveDemo
            ? "화면 확인용 가상 발전기·예측·실측입니다. 실제 성능이나 정산 결과가 아닙니다."
            : "DB에 저장된 기록을 조회합니다. 미연동·누락 항목은 대기 상태로 표시합니다."}
        </span>
      </div>
      {supportsDemo && (
        <>
          <div className="workspace-filters">
            <label className="filter-field">
              <span>VPP 그룹</span>
              <select
                aria-label="VPP 그룹"
                value={portfolio}
                onChange={(e) => setPortfolio(e.target.value)}
              >
                <option value="">전체 VPP</option>
                {options.data?.portfolios.map((g) => (
                  <option value={g.id} key={g.id}>
                    {g.name} · {g.version}
                  </option>
                ))}
              </select>
            </label>
            <label className="filter-field">
              <span>예측 모델</span>
              <select
                aria-label="예측 모델"
                value={model}
                onChange={(e) => setModel(e.target.value)}
              >
                <option value="">
                  {options.data?.models.length ? "기본 모델" : "모델 연결 대기"}
                </option>
                {options.data?.models.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.version}
                    {m.active ? " · 적용 중" : ""}
                  </option>
                ))}
              </select>
            </label>
            {page !== "home" && (
              <label className="filter-field">
                <span>
                  {page === "forecast" ? "예측 대상일" : "조회 기준일"}
                </span>
                <input
                  type="date"
                  min="2000-01-01"
                  max="2100-12-31"
                  aria-label={
                    page === "forecast" ? "예측 대상일" : "조회 기준일"
                  }
                  value={selectedDate}
                  onChange={(e) => {
                    if (e.target.validity.valid && e.target.value)
                      (page === "forecast" ? setForecastDate : setDate)(
                        e.target.value,
                      );
                  }}
                />
              </label>
            )}
            {["results", "portfolios"].includes(page) && (
              <div className="filter-field">
                <span>조회 단위</span>
                <div className="segmented">
                  {Object.entries(units).map(([key, label]) => (
                    <button
                      key={key}
                      aria-pressed={period === key}
                      onClick={() => setPeriod(key)}
                    >
                      {label}
                    </button>
                  ))}
                </div>
              </div>
            )}
          </div>
          {options.error && (
            <div className="error" role="alert">
              선택 목록 {options.error}
            </div>
          )}
        </>
      )}
      {page === "home" && (
        <Home
          today={data}
          tomorrow={tomorrow}
          navigate={navigate}
          groups={options.data?.portfolios || []}
          setPortfolio={setPortfolio}
          portfolio={portfolio}
          demo={effectiveDemo}
        />
      )}
      {page === "forecast" && (
        <ResourceState resource={data}>
          <div className="section-intro">
            <div>
              <span className="eyebrow">DAY-AHEAD FORECAST</span>
              <h2>{dateLabel(forecastDate)} 발전 계획</h2>
            </div>
            <span className="quiet-badge">
              {forecastDate === day(1) ? "내일" : "선택한 대상일"}
            </span>
          </div>
          <EnergyStats data={data.data} forecastOnly />
          <Panel title="시간대별 예측 발전량" eyebrow="HOURLY FORECAST">
            <EnergyChart
              series={(data.data?.series || []).map((r) => ({
                ...r,
                actual_kwh: null,
              }))}
              period="day"
            />
            <p className="workspace-note">
              {data.data?.prediction_policy} · 운영 마감 확정 전의 임시 서비스
              기준입니다.
            </p>
            <Warnings data={data.data} />
          </Panel>
          <Panel title="발전기별 예측">
            <PlantTable
              plants={data.data?.plants}
              groups={options.data?.portfolios}
              forecastOnly
            />
          </Panel>
        </ResourceState>
      )}
      {page === "results" && (
        <ResourceState resource={data}>
          <Results
            data={data.data}
            period={period}
            date={date}
            rules={options.data?.settlement_rules || []}
            rule={rule}
            setRule={setRule}
          />
        </ResourceState>
      )}
      {page === "plants" && (
        <ResourceState resource={assets}>
          <Assets data={assets.data} />
        </ResourceState>
      )}
      {page === "weather" && <Weather revision={revision} />}
      {page === "portfolios" && (
        <ResourceState resource={data}>
          <Portfolios
            data={data.data}
            groups={options.data?.portfolios || []}
            assets={assets}
            period={period}
            setPortfolio={setPortfolio}
            portfolio={portfolio}
            demo={effectiveDemo}
          />
        </ResourceState>
      )}
      {page === "manage" && <Management revision={revision} />}
    </div>
  );
}

function peak(data) {
  const rows = (data?.series || []).filter((r) => r.predicted_kwh != null);
  if (!rows.length) return { value: null, label: "예측 연결 대기" };
  const row = rows.reduce((best, r) =>
    r.predicted_kwh > best.predicted_kwh ? r : best,
  );
  return {
    value: row.predicted_kwh / 1000,
    label: `${row.label} · 확보된 시간 중 최대`,
  };
}
function EnergyStats({ data, forecastOnly = false }) {
  const s = data?.summary || {};
  return (
    <section className="kpis" aria-label="조회 결과 요약">
      <Stat
        label="예측 발전량"
        value={fmt(s.predicted_kwh == null ? null : s.predicted_kwh / 1000)}
        unit="MWh"
        note={`확보된 ${s.predicted_hours ?? 0}/${s.expected_hours ?? 0}시간 합계`}
        accent="gold"
      />
      {forecastOnly ? (
        <Stat
          label="예상 피크"
          value={fmt(peak(data).value)}
          unit="MWh"
          note={peak(data).label}
          accent="green"
        />
      ) : (
        <Stat
          label="실제 발전량"
          value={fmt(s.actual_kwh == null ? null : s.actual_kwh / 1000)}
          unit="MWh"
          note={`확보된 ${s.actual_hours ?? 0}/${s.expected_hours ?? 0}시간 합계`}
          accent="green"
        />
      )}
      <Stat
        label={forecastOnly ? "참여 발전기" : "시간별 평균 절대오차"}
        value={
          forecastOnly
            ? fmt(s.plant_count, 0)
            : fmt(s.mae_kwh == null ? null : s.mae_kwh / 1000)
        }
        unit={forecastOnly ? "개" : "MWh"}
        note={
          forecastOnly
            ? `선택 기간 구성 · ${fmt((s.capacity_kw || 0) / 1000)} MW`
            : `예측·실측이 모두 있는 ${s.paired_hours ?? 0}시간`
        }
      />
      <Stat
        label={forecastOnly ? "예측 생성 시각" : "저장된 정산금"}
        value={
          forecastOnly
            ? data?.is_demo
              ? "가상 예측"
              : data?.prediction_generated_at
                ? new Date(data.prediction_generated_at).toLocaleTimeString(
                    "ko-KR",
                    {
                      timeZone: "Asia/Seoul",
                      hour: "2-digit",
                      minute: "2-digit",
                    },
                  )
                : "대기"
            : fmt(s.settlement_krw, 0)
        }
        unit={forecastOnly ? "" : "원"}
        note={
          forecastOnly
            ? data?.prediction_generated_at
              ? dateLabel(data.prediction_generated_at)
              : "검증 가능한 생성 기록 기준"
            : moneyNote(data)
        }
      />
    </section>
  );
}
function Home({
  today,
  tomorrow,
  navigate,
  groups,
  portfolio,
  setPortfolio,
  demo,
}) {
  const s = today.data?.summary || {};
  const t = tomorrow.data?.summary || {};
  const tomorrowTotal =
    t.predicted_hours === t.expected_hours && t.expected_hours > 0
      ? t.predicted_kwh
      : null;
  return (
    <>
      <section className="forecast-hero">
        <div>
          <span className="eyebrow">TOMORROW'S ENERGY</span>
          <h2>내일, 얼마나 발전할까요?</h2>
          <p>{dateLabel(day(1))} · 하루 전 예측</p>
          <ResourceState resource={tomorrow}>
            <div className="hero-value">
              {fmt(tomorrowTotal == null ? null : tomorrowTotal / 1000)}
              <span>MWh</span>
            </div>
            <p>
              {tomorrowTotal == null
                ? "하루 전체의 예측이 확보되면 총량을 표시합니다."
                : `${t.plant_count}개 발전기 · ${t.predicted_hours}시간 예측 확보`}
            </p>
          </ResourceState>
        </div>
        <div className="hero-aside">
          <span className="quiet-badge">
            {demo ? "가상 예측" : "전일 18:00 · 임시 비교 기준"}
          </span>
          <button className="button" onClick={() => navigate("forecast")}>
            내일 예측 자세히 보기
          </button>
        </div>
      </section>
      <ResourceState resource={today}>
        <section className="kpis" aria-label="오늘 운영 요약">
          <Stat
            label="오늘 현재까지 실측"
            value={fmt(s.actual_kwh == null ? null : s.actual_kwh / 1000)}
            unit="MWh"
            note={`전체 발전기 실측 확보 ${s.actual_hours ?? 0}/${s.expected_hours ?? 0}시간`}
            accent="green"
          />
          <Stat
            label="오늘 비교 가능한 오차"
            value={fmt(s.mae_kwh == null ? null : s.mae_kwh / 1000)}
            unit="MWh"
            note={`하루 전 예측 · ${s.paired_hours ?? 0}시간 비교`}
          />
          <Stat
            label="오늘 저장된 정산금"
            value={fmt(s.settlement_krw, 0)}
            unit="원"
            note={moneyNote(today.data)}
          />
          <Stat
            label="예측·실측 비교 확보"
            value={
              s.expected_hours
                ? fmt((100 * s.paired_hours) / s.expected_hours, 0)
                : "—"
            }
            unit="%"
            note="하루 전체 대비 · 남은 시간 포함"
          />
        </section>
      </ResourceState>
      <div className="workspace-two-col">
        <Panel
          title="내일 시간별 예측"
          eyebrow="TOMORROW"
          action={
            <button
              className="text-button"
              onClick={() => navigate("forecast")}
            >
              상세 보기
            </button>
          }
        >
          <ResourceState resource={tomorrow}>
            <EnergyChart
              series={(tomorrow.data?.series || []).map((r) => ({
                ...r,
                actual_kwh: null,
              }))}
              period="day"
            />
          </ResourceState>
        </Panel>
        <Panel
          title="오늘 예측과 실제 발전량"
          eyebrow="TODAY"
          action={
            <button className="text-button" onClick={() => navigate("results")}>
              실적 · 정산 보기
            </button>
          }
        >
          <ResourceState resource={today}>
            <EnergyChart series={today.data?.series || []} period="day" />
          </ResourceState>
        </Panel>
      </div>
      <div className="workspace-two-col">
        <ResourceState resource={today}>
          <KoreaMap
            plants={today.data?.plants || []}
            groups={groups}
            selectedGroup={portfolio}
            onGroupChange={(id) => setPortfolio(String(id || ""))}
            isDemo={demo}
          />
        </ResourceState>
        <Panel title="운영 확인 항목" eyebrow="ATTENTION">
          <div className="attention-list">
            {!today.error && !today.loading && (
              <>
                <div>
                  <span
                    className={`status-pill ${s.paired_hours ? "good" : "pending"}`}
                  >
                    {s.paired_hours ? "조회 가능" : "연결 대기"}
                  </span>
                  <strong>예측·실측 비교</strong>
                  <p>
                    {s.paired_hours
                      ? `${s.paired_hours}개 시간 구간에서 비교할 수 있습니다.`
                      : "예측 모델과 발전기 실측이 모두 필요합니다."}
                  </p>
                </div>
                <div>
                  <span className="status-pill pending">
                    {s.settlement_krw == null ? "연결 대기" : "저장 기록"}
                  </span>
                  <strong>정산 결과</strong>
                  <p>
                    판매금액 추정·예측 인센티브·지급 확정액은 구분해서
                    확인합니다.
                  </p>
                </div>
                <Warnings data={today.data} />
              </>
            )}
            <button className="text-button" onClick={() => navigate("plants")}>
              발전기 신규 등장·장기 미수집 확인
            </button>
            <button className="text-button" onClick={() => navigate("manage")}>
              수집 실패·모델 기록 확인
            </button>
          </div>
        </Panel>
      </div>
    </>
  );
}
function Results({ data, period, date, rules, rule, setRule }) {
  const provisional =
    period !== "day" ||
    date >= day() ||
    (data?.summary.actual_hours || 0) < (data?.summary.expected_hours || 1);
  return (
    <>
      <div className="section-intro">
        <h2>
          {dateLabel(date)} 기준 · {units[period]} 실적
        </h2>
        <span className="quiet-badge">
          {provisional ? "잠정 · 확보 구간 기준" : "일일 실측 확보"}
        </span>
      </div>
      <EnergyStats data={data} />
      <Panel title="예측과 실측 비교">
        <EnergyChart series={data?.series || []} period={period} />
        <p className="workspace-note">
          {data?.prediction_policy} · 예측과 실측 총량의 확보 시간이 다르면 직접
          비교하지 않습니다.
        </p>
        <Warnings data={data} />
      </Panel>
      <div className="workspace-two-col">
        <Panel title="VPP 합산 예측오차">
          <EnergyChart
            series={data?.series || []}
            kind="error"
            period={period}
          />
        </Panel>
        <Panel title="시간별 SMP">
          <EnergyChart
            series={data?.series || []}
            kind="price"
            period={period}
          />
        </Panel>
      </div>
      <Panel
        title="정산 결과"
        action={
          <span className="quiet-badge">
            {data?.settlement?.is_simulation
              ? "시뮬레이션"
              : "저장 기록 · 지급 확정 아님"}
          </span>
        }
      >
        <label className="filter-field settlement-select">
          <span>정산 규칙 버전</span>
          <select
            aria-label="정산 규칙 버전"
            value={rule}
            onChange={(e) => setRule(e.target.value)}
          >
            <option value="">
              {rules.length === 1
                ? rules[0]
                : rules.length
                  ? "규칙 선택"
                  : "정산 규칙 연결 대기"}
            </option>
            {rules.length > 1 &&
              rules.map((r) => (
                <option key={r} value={r}>
                  {r}
                </option>
              ))}
          </select>
        </label>
        <div className="settlement-breakdown">
          <div>
            <span>SMP 판매금액 추정</span>
            <strong>계산 연동 대기</strong>
          </div>
          <div>
            <span>예측제도 인센티브</span>
            <strong>계산 연동 대기</strong>
          </div>
          <div>
            <span>저장된 정산 합계</span>
            <strong>{fmt(data?.summary.settlement_krw, 0)} 원</strong>
          </div>
        </div>
        <EnergyChart series={data?.series || []} kind="money" period={period} />
        <p className="workspace-note">
          정산 그래프는 선택한 규칙의 기존 저장 결과입니다. 위 하루 전 예측과의
          차수 연결은 아직 검증되지 않았습니다. 당일·불완전 구간은 잠정치이며
          실제 지급액 확정을 뜻하지 않습니다.
        </p>
      </Panel>
    </>
  );
}
function Portfolios({
  data,
  groups,
  assets,
  period,
  portfolio,
  setPortfolio,
  demo,
}) {
  const individual = data?.summary.individual_mae_kwh;
  const grouped = data?.summary.mae_kwh;
  const reduction =
    individual > 0 && grouped != null
      ? (100 * (individual - grouped)) / individual
      : null;
  return (
    <>
      <EnergyStats data={data} />
      <section className="settlement-breakdown" aria-label="VPP 오차 감소 비교">
        <div>
          <span>개별 절대오차 합 · 시간 평균</span>
          <strong>
            {fmt(individual == null ? null : individual / 1000)} MWh
          </strong>
        </div>
        <div>
          <span>VPP 합산 절대오차 · 시간 평균</span>
          <strong>{fmt(grouped == null ? null : grouped / 1000)} MWh</strong>
        </div>
        <div>
          <span>같은 구간의 오차 감소율</span>
          <strong>{fmt(reduction)} %</strong>
        </div>
      </section>
      <div className="portfolio-cards">
        {groups.map((g) => (
          <button
            className={`portfolio-card ${String(g.id) === portfolio ? "active" : ""}`}
            key={g.id}
            onClick={() =>
              setPortfolio(String(g.id) === portfolio ? "" : String(g.id))
            }
          >
            <span className="eyebrow">VPP PORTFOLIO</span>
            <h2>{g.name}</h2>
            <p>구성 버전 {g.version}</p>
            <span>등록 목표 용량 {fmt(g.target_capacity_kw / 1000)} MW</span>
          </button>
        ))}
      </div>
      <Panel title="선택한 VPP의 구성 발전기">
        <PlantTable plants={data?.plants} groups={groups} />
        <Warnings data={data} />
      </Panel>
      <div className="workspace-two-col">
        <Panel title="VPP 합산 후 예측오차">
          <EnergyChart
            series={data?.series || []}
            kind="error"
            period={period}
          />
          <p className="workspace-note">
            그룹 안에서 예측·실측을 각각 더한 뒤 절대오차를 구합니다. 위 비교는
            모든 구성원의 예측·실측이 확보된 동일 시간만 사용합니다. 오차 감소가
            정산금 증가를 보장하지는 않습니다.
          </p>
        </Panel>
        <Panel title="VPP 구성 적용 이력">
          {demo ? (
            <Empty title="실제 구성 이력은 실제 데이터에서 확인하세요">
              데모 그룹은 화면 확인용이며 운영 구성 이력이 아닙니다.
            </Empty>
          ) : (
            <ResourceState resource={assets}>
              <PortfolioHistory
                plants={assets.data?.plants || []}
                portfolio={portfolio}
              />
            </ResourceState>
          )}
        </Panel>
      </div>
    </>
  );
}
function Management({ revision }) {
  const [view, setView] = useState("job-runs");
  return (
    <>
      <div className="management-intro">
        <strong>운영 상태와 모델을 한곳에서</strong>
        <p>
          작업 기록에서 수집 성공·실패를 확인하고, 모델 지표와 학습 이력을
          살펴보세요. 이 화면은 실제 DB 관리 기록을 조회합니다.
        </p>
      </div>
      <div className="management-tabs" role="group" aria-label="관리 기록 종류">
        {Object.entries(recordViews).map(([key, info]) => (
          <button
            className="button"
            key={key}
            aria-pressed={view === key}
            onClick={() => setView(key)}
          >
            {info.title}
          </button>
        ))}
      </div>
      <RecordsPanel view={view} refreshRevision={revision} />
    </>
  );
}
