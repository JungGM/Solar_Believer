import React, { useState } from "react";
import { createRoot } from "react-dom/client";
import Dashboard from "./Dashboard";
import RecordsPanel, { recordViews } from "./components/RecordsPanel";
import "./styles.css";

function App() {
  const [view, setView] = useState("dashboard");
  return (
    <div className="shell">
      <aside className="sidebar">
        <a className="brand" href="/" aria-label="Solar Believer 홈">
          <span className="sun">☀</span>
          <span>
            Solar
            <br />
            Believer<span className="brand-small">ENERGY WORKSPACE</span>
          </span>
        </a>
        <div className="nav-caption">WORKSPACE</div>
        <nav aria-label="주 메뉴">
          <button
            className={view === "dashboard" ? "selected" : ""}
            onClick={() => setView("dashboard")}
          >
            <span aria-hidden="true">◫</span>통합 대시보드
          </button>
          <div className="nav-divider" />
          <div className="nav-caption">데이터 · 모델 관리</div>
          {Object.entries(recordViews).map(([key, info], index) => (
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
          <span className="dot" /> 로컬 개발 환경
          <p>
            발전소별 예측
            <br />
            20MW 목표 VPP 포트폴리오
          </p>
        </div>
      </aside>
      <main>
        <header className="topbar">
          <div>
            Solar Believer<span>/</span>
            {view === "dashboard" ? "Overview" : "Data explorer"}
          </div>
          <span className="environment-badge">
            <i /> DEVELOPMENT
          </span>
        </header>
        {view === "dashboard" ? <Dashboard /> : <RecordsPanel view={view} />}
        <footer>
          <span>SOLAR BELIEVER · VPP WORKSPACE</span>
          <span>한국 표준시 KST · 발전량 kWh / MWh · 가격 원/kWh</span>
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
