import React from "react";
import {
  ResponsiveContainer,
  LineChart,
  Line,
  BarChart,
  Bar,
  CartesianGrid,
  XAxis,
  YAxis,
  Tooltip,
  Legend,
  ReferenceLine,
} from "recharts";

const fmt = (value) =>
  value === null || value === undefined
    ? "—"
    : Number(value).toLocaleString("ko-KR", { maximumFractionDigits: 2 });

function Frame({
  children,
  caption,
  hasData,
  emptyText = "선택한 조건의 데이터가 아직 없습니다.",
}) {
  return (
    <>
      <div className="chart-area">
        {hasData ? (
          children
        ) : (
          <div className="chart-empty">
            <span aria-hidden="true">⌁</span>
            <strong>{emptyText}</strong>
            <p>등록된 값부터 표시하며 누락값은 0으로 채우지 않습니다.</p>
          </div>
        )}
      </div>
      {caption && <p className="chart-caption">{caption}</p>}
    </>
  );
}

export default function EnergyChart({
  series,
  kind = "generation",
  period,
  moneyField = "settlement_krw",
}) {
  const data = series.map((r) => ({
    ...r,
    predicted_mwh: r.predicted_kwh === null ? null : r.predicted_kwh / 1000,
    actual_mwh: r.actual_kwh === null ? null : r.actual_kwh / 1000,
    error_mwh:
      r.absolute_error_kwh === null ? null : r.absolute_error_kwh / 1000,
  }));
  const axis = (
    <>
      <CartesianGrid strokeDasharray="3 4" vertical={false} stroke="#e7ebe3" />
      <XAxis
        dataKey="label"
        tickLine={false}
        axisLine={false}
        tick={{ fill: "#829078", fontSize: 10 }}
        minTickGap={period === "month" ? 25 : 18}
      />
      <YAxis
        tickLine={false}
        axisLine={false}
        tick={{ fill: "#829078", fontSize: 10 }}
        width={48}
      />
    </>
  );
  if (kind === "price")
    return (
      <Frame
        hasData={data.some((r) => r.smp_land !== null || r.smp_jeju !== null)}
        caption={
          period === "day"
            ? "거래시간별 SMP · 원/kWh"
            : "알려진 시간별 가격의 단순 평균 · 원/kWh"
        }
      >
        <ResponsiveContainer width="100%" height="100%">
          <LineChart
            data={data}
            margin={{ top: 12, right: 16, left: 0, bottom: 0 }}
          >
            {axis}
            <Tooltip formatter={(v, name) => [`${fmt(v)} 원/kWh`, name]} />
            <Legend iconType="plainline" wrapperStyle={{ fontSize: 11 }} />
            {data.some((r) => r.smp_land !== null) && (
              <Line
                name="육지 SMP"
                dataKey="smp_land"
                stroke="#6f83a3"
                strokeWidth={2}
                dot={false}
                connectNulls={false}
                isAnimationActive={false}
              />
            )}
            {data.some((r) => r.smp_jeju !== null) && (
              <Line
                name="제주 SMP"
                dataKey="smp_jeju"
                stroke="#a97aab"
                strokeWidth={2}
                dot={false}
                connectNulls={false}
                isAnimationActive={false}
              />
            )}
          </LineChart>
        </ResponsiveContainer>
      </Frame>
    );
  if (kind === "error")
    return (
      <Frame
        hasData={data.some((r) => r.error_mwh !== null)}
        caption="VPP 내 합산 후 절대오차 · 여러 VPP의 절대오차를 합계 · MWh"
      >
        <ResponsiveContainer width="100%" height="100%">
          <BarChart
            data={data}
            margin={{ top: 12, right: 16, left: 0, bottom: 0 }}
          >
            {axis}
            <Tooltip formatter={(v) => [`${fmt(v)} MWh`, "절대오차"]} />
            <Bar
              dataKey="error_mwh"
              name="절대오차"
              fill="#c7ae77"
              radius={[3, 3, 0, 0]}
              isAnimationActive={false}
            />
          </BarChart>
        </ResponsiveContainer>
      </Frame>
    );
  if (kind === "money")
    return (
      <Frame
        hasData={data.some((r) => r[moneyField] !== null)}
        emptyText="저장된 정산 계산 결과가 없습니다."
        caption="선택한 규칙 버전의 저장 결과만 조회 · 원"
      >
        <ResponsiveContainer width="100%" height="100%">
          <BarChart
            data={data}
            margin={{ top: 12, right: 16, left: 0, bottom: 0 }}
          >
            {axis}
            <ReferenceLine y={0} stroke="#b8c5b0" />
            <Tooltip
              formatter={(v) => [
                `${fmt(v)} 원`,
                moneyField === "loss_krw" ? "금전적 손실" : "정산금",
              ]}
            />
            <Bar
              dataKey={moneyField}
              fill="#72907b"
              radius={[3, 3, 0, 0]}
              isAnimationActive={false}
            />
          </BarChart>
        </ResponsiveContainer>
      </Frame>
    );
  return (
    <Frame
      hasData={data.some(
        (r) => r.predicted_mwh !== null || r.actual_mwh !== null,
      )}
      caption={
        period === "day"
          ? "시간별 발전량 · MWh"
          : "모든 참여 발전소의 값이 있는 구간만 일·월 합계 표시 · MWh"
      }
    >
      <ResponsiveContainer width="100%" height="100%">
        <LineChart
          data={data}
          margin={{ top: 12, right: 16, left: 0, bottom: 0 }}
        >
          {axis}
          <Tooltip formatter={(v, name) => [`${fmt(v)} MWh`, name]} />
          <Legend iconType="plainline" wrapperStyle={{ fontSize: 11 }} />
          <Line
            name="실제 발전량"
            dataKey="actual_mwh"
            stroke="#377659"
            strokeWidth={2.5}
            dot={false}
            connectNulls={false}
            isAnimationActive={false}
          />
          <Line
            name="예측 발전량"
            dataKey="predicted_mwh"
            stroke="#c9a053"
            strokeWidth={2.2}
            strokeDasharray="5 4"
            dot={false}
            connectNulls={false}
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </Frame>
  );
}
