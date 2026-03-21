async function postJSON(url, body) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

  let json = null;
  try {
    json = await res.json();
  } catch (e) {
    json = { ok: false, error: "서버 응답(JSON) 파싱 실패" };
  }

  return { httpOk: res.ok, json };
}

function renderPretty(obj) {
  return JSON.stringify(obj, null, 2);
}

document.addEventListener("DOMContentLoaded", () => {
  const btnMatch = document.getElementById("btnMatch");
  const btnAutoAssign = document.getElementById("btnAutoAssign");
  const btnLoadRanks = document.getElementById("btnLoadRanks");

  if (btnMatch) {
    btnMatch.addEventListener("click", async () => {
      const orderIdEl = document.getElementById("orderId");
      const orderId = (orderIdEl.value || "").trim();

      const out = document.getElementById("matchResult");
      if (!orderId) {
        out.textContent = "❌ 주문번호를 입력하세요. 예: 26_1";
        return;
      }

      out.textContent = "요청 중...";

      // ✅ 문자열 주문번호 그대로 전달
      const { httpOk, json } = await postJSON("/api/match", { order_id: orderId });

      if (!httpOk || !json.ok) {
        out.textContent = `❌ ${json.error || "실패"}`;
        return;
      }

      out.textContent = "✅ 매칭 성공\n" + renderPretty(json.data);
    });

    // 엔터로도 실행되게(편의)
    const orderIdEl = document.getElementById("orderId");
    if (orderIdEl) {
      orderIdEl.addEventListener("keydown", (e) => {
        if (e.key === "Enter") btnMatch.click();
      });
    }
  }

  if (btnAutoAssign) {
    btnAutoAssign.addEventListener("click", async () => {
      const out = document.getElementById("autoResult");
      out.textContent = "요청 중...";

      const { httpOk, json } = await postJSON("/api/auto_assign", {});
      if (!httpOk || !json.ok) {
        out.textContent = `❌ ${json.error || "실패"}`;
        return;
      }
      out.textContent = "✅ 자동 배차 성공\n" + renderPretty(json.data);
    });
  }

  if (btnLoadRanks) {
    btnLoadRanks.addEventListener("click", async () => {
      const wrap = document.getElementById("tableWrap");
      const err = document.getElementById("rankErr");

      err.textContent = "";
      wrap.innerHTML = "불러오는 중...";

      const res = await fetch("/api/ranks");
      const json = await res.json();

      if (!res.ok || !json.ok) {
        wrap.innerHTML = "";
        err.textContent = `❌ ${json.error || "실패"}`;
        return;
      }

      const rows = json.data || [];
      if (!rows.length) {
        wrap.innerHTML = "데이터가 없습니다.";
        return;
      }

      const cols = Object.keys(rows[0]);
      let html = "<table><thead><tr>";
      cols.forEach(c => (html += `<th>${c}</th>`));
      html += "</tr></thead><tbody>";

      rows.forEach(r => {
        html += "<tr>";
        cols.forEach(c => (html += `<td>${r[c] ?? ""}</td>`));
        html += "</tr>";
      });

      html += "</tbody></table>";
      wrap.innerHTML = html;
    });
  }
});

const btnSentiment = document.getElementById("btnSentiment");
if (btnSentiment) {
  btnSentiment.addEventListener("click", async () => {
    const text = (document.getElementById("reviewText").value || "").trim();
    const out = document.getElementById("sentimentResult");

    if (!text) {
      out.textContent = "❌ 리뷰를 입력하세요.";
      return;
    }

    out.textContent = "분석 중...";
    const { httpOk, json } = await postJSON("/api/sentiment", { text });

    if (!httpOk || !json.ok) {
      out.textContent = `❌ ${json.error || "실패"}`;
      return;
    }

    out.textContent = "✅ 분석 결과\n" + JSON.stringify(json.data, null, 2);
  });
}
