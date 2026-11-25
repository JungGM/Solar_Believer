import React, { useState } from "react";
import KoreaMap from "./KoreaMap";
import {
  useResource,
  Panel,
  Empty,
  ResourceState,
  Stat,
  fmt,
  day,
  dateLabel,
  time,
} from "./WorkspaceShared";

const stateLabels = {
  no_data: "실측 미등록",
  stale: "장기 미수집",
  recent: "최근 실측 있음",
};
export function PortfolioHistory({ plants, portfolio = "" }) {
  const entries = plants.flatMap((p) =>
    p.memberships
      .filter((m) => !portfolio || String(m.portfolio_id) === portfolio)
      .map((m, i) => (
        <li key={`${p.id}-${i}`}>
          <strong>
            {p.name} · {m.portfolio_name}
          </strong>
          <span>
            {dateLabel(m.valid_from)} ~{" "}
            {m.valid_to ? dateLabel(m.valid_to) : "종료 미등록"}
          </span>
        </li>
      )),
  );
  return entries.length ? (
    <ul className="history-list">{entries}</ul>
  ) : (
    <Empty title="등록된 구성 적용 이력이 없습니다" />
  );
}
export function Assets({ data }) {
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("");
  const [selected, setSelected] = useState("");
  const [group, setGroup] = useState("");
  const plants = data?.plants || [];
  const now = Date.now();
  const activeMemberships = (p) =>
    p.memberships.filter(
      (m) =>
        new Date(m.valid_from).getTime() <= now &&
        (!m.valid_to || new Date(m.valid_to).getTime() > now),
    );
  const filtered = plants.filter(
    (p) =>
      `${p.name} ${p.id}`.toLowerCase().includes(search.toLowerCase()) &&
      (!status || p.status === status) &&
      (!group ||
        activeMemberships(p).some((m) => String(m.portfolio_id) === group)),
  );
  const focused = plants.find((p) => p.id === selected);
  const mappedPlants = filtered.map((p) => ({
    ...p,
    portfolio_ids: activeMemberships(p).map((m) => m.portfolio_id),
  }));
  const groups = [
    ...new Map(
      plants
        .flatMap((p) => p.memberships)
        .map((m) => [
          m.portfolio_id,
          { id: m.portfolio_id, name: m.portfolio_name },
        ]),
    ).values(),
  ];
  return (
    <>
      <section className="kpis">
        <Stat
          label="등록 발전기"
          value={plants.length}
          unit="개"
          note="실제 DB 등록 자원"
          accent="green"
        />
        <Stat
          label="좌표 확보"
          value={
            plants.filter((p) => p.latitude != null && p.longitude != null)
              .length
          }
          unit="개"
          note="좌표가 없는 자원도 목록에 포함"
        />
        <Stat
          label="최근 7일 내 최초 관측"
          value={
            plants.filter(
              (p) =>
                p.first_seen &&
                now - new Date(p.first_seen).getTime() < 7 * 86400000,
            ).length
          }
          unit="개"
          note="저장 자료 기준 · 신규 설비 확정 아님"
        />
        <Stat
          label="장기 미수집"
          value={plants.filter((p) => p.status === "stale").length}
          unit="개"
          note="최종 실측 대상 시각이 7일 이전"
        />
      </section>
      <div className="workspace-filters">
        <label className="filter-field">
          <span>발전기 검색</span>
          <input
            aria-label="발전기 검색"
            placeholder="발전기 이름 또는 ID"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </label>
        <label className="filter-field">
          <span>데이터 상태</span>
          <select
            aria-label="데이터 상태"
            value={status}
            onChange={(e) => setStatus(e.target.value)}
          >
            <option value="">전체 상태</option>
            {Object.entries(stateLabels).map(([key, label]) => (
              <option key={key} value={key}>
                {label}
              </option>
            ))}
          </select>
        </label>
      </div>
      <div className="workspace-two-col">
        <KoreaMap
          plants={mappedPlants}
          groups={groups}
          selectedGroup={group}
          onGroupChange={(id) => setGroup(String(id || ""))}
          isDemo={false}
        />
        <Panel title={focused ? focused.name : "발전기 상세 · 구성 이력"}>
          {focused ? (
            <>
              <dl className="asset-details">
                <dt>발전기 ID</dt>
                <dd>{focused.id}</dd>
                <dt>위도 / 경도</dt>
                <dd>
                  {focused.latitude ?? "미등록"} /{" "}
                  {focused.longitude ?? "미등록"}
                </dd>
                <dt>실측 출처</dt>
                <dd>{focused.source}</dd>
                <dt>최초 관측 대상일</dt>
                <dd>{dateLabel(focused.first_seen)}</dd>
                <dt>최종 관측 대상일</dt>
                <dd>{dateLabel(focused.last_seen)}</dd>
                <dt>최근 수집 시각</dt>
                <dd>{time(focused.last_collected)}</dd>
              </dl>
              <h3>VPP 소속 이력</h3>
              <PortfolioHistory plants={[focused]} />
            </>
          ) : (
            <Empty title="목록에서 발전기를 선택하세요">
              설비 정보와 VPP 소속 기간을 확인할 수 있습니다.
            </Empty>
          )}
        </Panel>
      </div>
      <Panel
        title="발전기 목록"
        action={<span className="quiet-badge">{filtered.length}개</span>}
      >
        {filtered.length ? (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>발전기</th>
                  <th>용량</th>
                  <th>최초 관측</th>
                  <th>최종 관측</th>
                  <th>상태</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((p) => (
                  <tr key={p.id}>
                    <td>
                      <button
                        className="text-button"
                        onClick={() => setSelected(p.id)}
                      >
                        {p.name}
                      </button>
                      <span className="cell-subtitle">{p.id}</span>
                    </td>
                    <td>{fmt(p.capacity_kw / 1000)} MW</td>
                    <td>{dateLabel(p.first_seen)}</td>
                    <td>{dateLabel(p.last_seen)}</td>
                    <td>
                      <span
                        className={`status-pill ${p.status === "recent" ? "good" : "pending"}`}
                      >
                        {stateLabels[p.status]}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <Empty
            title={
              plants.length
                ? "검색 조건에 맞는 발전기가 없습니다"
                : "등록된 발전기가 없습니다"
            }
          />
        )}
        <p className="workspace-note">
          {data?.history_policy} 실제 폐지와 설비·위치·용량 변경 이력은 확인
          근거와 변경 기록이 확보된 뒤 표시합니다.
        </p>
      </Panel>
    </>
  );
}

const fields = [
  ["temperature_c", "TMP", "기온", "°C"],
  ["humidity_pct", "REH", "습도", "%"],
  ["wind_speed_ms", "WSD", "풍속", "m/s"],
  ["solar_radiation_mj", null, "일사량", "MJ/m²"],
  ["cloud_cover_10", null, "운량", "할"],
  [null, "SKY", "하늘상태 코드", ""],
];
export function Weather({ revision }) {
  const [date, setDate] = useState(day);
  const [scope, setScope] = useState("");
  const [kind, setKind] = useState("all");
  const q = new URLSearchParams({ date });
  if (scope) q.set("scope_id", scope);
  const resource = useResource(`workspace/weather?${q}`, revision);
  const records = (resource.data?.records || []).filter(
    (r) => kind === "all" || r.kind === kind,
  );
  return (
    <>
      <div className="workspace-filters">
        <label className="filter-field">
          <span>대상일</span>
          <input
            aria-label="날씨 대상일"
            type="date"
            min="2000-01-01"
            max="2100-12-31"
            value={date}
            onChange={(e) => {
              if (e.target.validity.valid && e.target.value)
                setDate(e.target.value);
            }}
          />
        </label>
        <label className="filter-field">
          <span>지점</span>
          <select
            aria-label="기상 지점"
            value={scope}
            onChange={(e) => setScope(e.target.value)}
          >
            <option value="">전체 지점</option>
            {resource.data?.scopes.map((id) => (
              <option key={id}>{id}</option>
            ))}
          </select>
        </label>
        <label className="filter-field">
          <span>자료 종류</span>
          <select
            aria-label="기상 자료 종류"
            value={kind}
            onChange={(e) => setKind(e.target.value)}
          >
            <option value="all">예보와 관측</option>
            <option value="weather_forecast">예보</option>
            <option value="weather_observation">실제 관측</option>
          </select>
        </label>
      </div>
      <ResourceState resource={resource}>
        <Panel
          title="예보 · 실제 관측 기록"
          action={<span className="quiet-badge">{records.length}건</span>}
        >
          <p className="workspace-note">{resource.data?.mapping_status}</p>
          {resource.data?.truncated && (
            <p className="quality-warning">
              최대 500건만 표시합니다. 지점을 선택해 범위를 좁혀 주세요.
            </p>
          )}
          {records.length ? (
            <div className="table-wrap weather-table">
              <table>
                <thead>
                  <tr>
                    <th>지점 / 출처</th>
                    <th>종류</th>
                    <th>대상 시각 / 발표 시각</th>
                    {fields.map(([, , label, unit]) => (
                      <th key={label}>
                        {label}
                        {unit && <span className="cell-subtitle">{unit}</span>}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {records.map((r) => (
                    <tr key={r.id}>
                      <td>
                        {r.scope_id}
                        <span className="cell-subtitle">{r.source}</span>
                      </td>
                      <td>
                        <span
                          className={`status-pill ${r.kind === "weather_forecast" ? "pending" : "good"}`}
                        >
                          {r.kind === "weather_forecast" ? "예보" : "관측"}
                        </span>
                      </td>
                      <td>
                        {time(r.target_at)}
                        <span className="cell-subtitle">
                          발표 {r.issued_at ? time(r.issued_at) : "미제공"}
                        </span>
                      </td>
                      {fields.map(([obs, fcst, label]) => (
                        <td key={label}>
                          {r.values[obs] ?? r.values[fcst] ?? "—"}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <Empty title="선택한 조건의 기상 기록이 없습니다">
              예보·관측 수집과 DB 적재 후 표시됩니다. 파일로 수집된 자료는
              자동으로 이 화면에 연결되지 않습니다.
            </Empty>
          )}
          <p className="workspace-note">
            운량(0~10)과 하늘상태 코드(SKY)는 서로 다른 지표입니다. 기상
            오차·발전소별 비교 그래프는 지점 연결과 단위 검증 후 제공됩니다.
          </p>
        </Panel>
      </ResourceState>
    </>
  );
}
