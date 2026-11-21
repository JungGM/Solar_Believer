import React, { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import Workspace, { pages } from "./Workspace";
import "./styles.css";
import "./workspace.css";

function currentPage() {
  const value = new URLSearchParams(window.location.search).get("page");
  return pages.some((p) => p.id === value) ? value : "home";
}

function App() {
  const [view, setView] = useState(currentPage);
  const [demo, setDemo] = useState(
    () => new URLSearchParams(window.location.search).get("demo") === "true",
  );
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    const handle = () => {
      setView(currentPage());
      setDemo(
        new URLSearchParams(window.location.search).get("demo") === "true",
      );
    };
    window.addEventListener("popstate", handle);
    return () => window.removeEventListener("popstate", handle);
  }, []);
  function navigate(next, nextDemo = demo) {
    const query = new URLSearchParams(window.location.search);
    query.set("page", next);
    query.set("demo", String(nextDemo));
    window.history.pushState({}, "", `${window.location.pathname}?${query}`);
    setView(next);
    setDemo(nextDemo);
  }
  return (
    <div className="shell workspace-shell">
      <aside className="sidebar">
        <a
          className="brand"
          href="?page=home"
          onClick={(e) => {
            e.preventDefault();
            navigate("home");
          }}
          aria-label="Solar Believer 홈"
        >
          <span className="sun">☀</span>
          <span>
            Solar
            <br />
            Believer<span className="brand-small">ENERGY WORKSPACE</span>
          </span>
        </a>
        <div className="nav-caption">발전부터 정산까지</div>
        <nav aria-label="주 메뉴">
          {pages.map((info) => (
            <button
              key={info.id}
              className={view === info.id ? "selected" : ""}
              aria-current={view === info.id ? "page" : undefined}
              onClick={() => navigate(info.id)}
            >
              <span className="nav-icon" aria-hidden="true">
                {info.icon}
              </span>
              {info.label}
            </button>
          ))}
        </nav>
        <div className="aside-note">
          <span className="dot" /> 로컬 개발 환경
          <p>
            발전소별 예측
            <br />
            기록에 근거한 VPP 운영
          </p>
        </div>
      </aside>
      <main>
        <header className="topbar">
          <div>
            Solar Believer<span>/</span>
            {pages.find((p) => p.id === view)?.label}
          </div>
          <span className="environment-badge">
            <i /> DEVELOPMENT
          </span>
        </header>
        <Workspace
          page={view}
          demo={demo}
          revision={revision}
          navigate={navigate}
          onRefresh={() => setRevision((r) => r + 1)}
        />
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
