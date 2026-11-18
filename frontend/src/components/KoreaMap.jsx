import React, { useMemo, useState } from "react";
import { geoMercator, geoPath } from "d3-geo";
import korea from "../data/korea.json";

const colors = ["#367355", "#b98a42", "#7a78ad", "#4c839c", "#b66f67"];
export function groupColor(id, groups) {
  return colors[
    Math.max(
      0,
      groups.findIndex((g) => g.id === id),
    ) % colors.length
  ];
}

export default function KoreaMap({
  plants,
  groups,
  selectedGroup,
  onGroupChange,
  isDemo,
}) {
  const [focused, setFocused] = useState(null);
  const projection = useMemo(
    () =>
      geoMercator().fitExtent(
        [
          [32, 18],
          [438, 372],
        ],
        korea,
      ),
    [],
  );
  const path = useMemo(() => geoPath(projection)(korea), [projection]);
  const mapped = plants.filter(
    (p) => Number.isFinite(p.latitude) && Number.isFinite(p.longitude),
  );
  const selected = plants.find((p) => p.id === focused);
  const cities = [
    [126.98, 37.56, "서울"],
    [127.38, 36.35, "대전"],
    [126.85, 35.16, "광주"],
    [129.07, 35.18, "부산"],
    [126.53, 33.5, "제주"],
  ];
  return (
    <section className="panel map-panel">
      <div className="panel-heading">
        <div>
          <span className="eyebrow">PORTFOLIO MAP</span>
          <h2>발전소 위치</h2>
        </div>
        <span className="quiet-badge">{mapped.length}개 지점</span>
      </div>
      <div className="map-surface">
        <svg
          viewBox="0 0 470 392"
          role="group"
          aria-label="대한민국 지도와 선택한 VPP의 발전소 위치"
        >
          <defs>
            <pattern
              id="map-dots"
              width="18"
              height="18"
              patternUnits="userSpaceOnUse"
            >
              <circle cx="1" cy="1" r="0.7" fill="#c9d3c4" />
            </pattern>
          </defs>
          <rect width="470" height="392" fill="url(#map-dots)" />
          <path d={path} fill="#e3eadc" stroke="#a3b59c" strokeWidth="1.2" />
          {cities.map(([lon, lat, name]) => {
            const [x, y] = projection([lon, lat]);
            return (
              <g key={name} className="city-label" aria-hidden="true">
                <circle cx={x} cy={y} r="2" />
                <text x={x + 7} y={y + 4}>
                  {name}
                </text>
              </g>
            );
          })}
          {mapped.map((plant) => {
            const [x, y] = projection([plant.longitude, plant.latitude]);
            const color = groupColor(plant.portfolio_ids[0], groups);
            return (
              <g
                key={plant.id}
                role="button"
                tabIndex="0"
                aria-label={`${plant.name}, ${(plant.capacity_kw / 1000).toFixed(1)}MW${isDemo ? ", 가상 지점" : ""}`}
                className="plant-marker"
                onClick={() => setFocused(plant.id)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    setFocused(plant.id);
                  }
                }}
              >
                <title>
                  {plant.name} · {(plant.capacity_kw / 1000).toFixed(1)}MW
                  {isDemo ? " · 가상 지점" : ""}
                </title>
                <circle cx={x} cy={y} r="14" fill="transparent" />
                <circle
                  cx={x}
                  cy={y}
                  r={focused === plant.id ? 11 : 9}
                  fill={color}
                  fillOpacity="0.15"
                />
                <circle
                  cx={x}
                  cy={y}
                  r={focused === plant.id ? 5.5 : 4.5}
                  fill={color}
                  stroke="#fff"
                  strokeWidth="1.7"
                />
              </g>
            );
          })}
          {mapped.length === 0 && (
            <text x="235" y="212" textAnchor="middle" className="map-empty">
              등록된 발전소 좌표가 없습니다
            </text>
          )}
        </svg>
        <span className="north-mark" aria-hidden="true">
          N ↑
        </span>
        {isDemo && <span className="map-demo">가상 발전소 위치</span>}
      </div>
      <div className="map-selection" aria-live="polite">
        {selected ? (
          <>
            <strong>{selected.name}</strong>
            <span>
              {(selected.capacity_kw / 1000).toFixed(1)} MW ·{" "}
              {selected.smp_zone === "land" ? "육지" : "제주"}
            </span>
          </>
        ) : (
          <span>지점을 선택하면 발전소 정보를 확인할 수 있습니다.</span>
        )}
      </div>
      <div className="map-legend">
        {groups.map((g) => (
          <button
            key={g.id}
            className={
              String(g.id) === selectedGroup
                ? "legend-group active"
                : "legend-group"
            }
            onClick={() => onGroupChange(String(g.id))}
          >
            <i style={{ background: groupColor(g.id, groups) }} />
            {g.name}
          </button>
        ))}
      </div>
      <div className="map-credit">
        Natural Earth · 개략도 · 일부 작은 섬 생략
      </div>
    </section>
  );
}
