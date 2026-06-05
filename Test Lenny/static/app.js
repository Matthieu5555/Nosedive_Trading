const state = {
  snapshot: null,
  risk: null,
  status: null,
};

const money = new Intl.NumberFormat("fr-FR", { maximumFractionDigits: 2 });
const pct = new Intl.NumberFormat("fr-FR", { maximumFractionDigits: 2 });

document.querySelectorAll(".tab").forEach((button) => {
  button.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((item) => item.classList.remove("active"));
    document.querySelectorAll(".panel").forEach((item) => item.classList.remove("active"));
    button.classList.add("active");
    document.getElementById(button.dataset.tab).classList.add("active");
  });
});

document.getElementById("refreshButton").addEventListener("click", refreshAll);
document.getElementById("previewOrder").addEventListener("click", () => submitOrder("preview"));
document.getElementById("orderForm").addEventListener("submit", (event) => {
  event.preventDefault();
  submitOrder("place");
});

async function refreshAll() {
  const symbols = document.getElementById("symbolsInput").value;
  const [status, snapshot, risk, orders] = await Promise.all([
    getJson("/api/status"),
    getJson(`/api/snapshot?symbols=${encodeURIComponent(symbols)}`),
    getJson(`/api/risk?symbols=${encodeURIComponent(symbols)}`),
    getJson("/api/orders"),
  ]);
  state.status = status;
  state.snapshot = snapshot;
  state.risk = risk.risk;
  renderStatus(status, snapshot);
  renderData(snapshot);
  renderRisk(risk.risk);
  renderOrders(orders.orders);
}

async function getJson(path) {
  const response = await fetch(path);
  return response.json();
}

function renderStatus(status, snapshot) {
  const mode = snapshot.mode || status.mode;
  document.getElementById("statusLine").textContent =
    `${mode.toUpperCase()} | code ${status.codeVersion} | config ${status.configHash} | raw events ${status.rawEvents}`;
  document.getElementById("modeMetric").textContent = mode.toUpperCase();
  document.getElementById("underlyingMetric").textContent = snapshot.underlyings.length;
  document.getElementById("optionMetric").textContent = snapshot.options.length;
  document.getElementById("qualityMetric").textContent = snapshot.quality.status.toUpperCase();
  document.getElementById("provenanceText").textContent =
    `source ${snapshot.provenance.source} | calc ${snapshot.provenance.calcTs}`;
}

function renderData(snapshot) {
  const underlyings = document.getElementById("underlyingsTable");
  underlyings.innerHTML = snapshot.underlyings.map((quote) => `
    <tr>
      <td>${quote.symbol}</td>
      <td>${quote.asset_class}</td>
      <td class="num">${money.format(quote.bid)}</td>
      <td class="num">${money.format(quote.ask)}</td>
      <td class="num">${money.format(quote.last)}</td>
      <td class="num">${money.format(quote.volume)}</td>
    </tr>
  `).join("");

  const options = document.getElementById("optionsTable");
  options.innerHTML = snapshot.options.slice(0, 260).map((option) => `
    <tr>
      <td>${option.underlying}</td>
      <td>${option.expiry}</td>
      <td class="num">${money.format(option.strike)}</td>
      <td>${option.right}</td>
      <td class="num">${money.format(option.bid)}</td>
      <td class="num">${money.format(option.ask)}</td>
      <td class="num">${pct.format(option.implied_vol * 100)}%</td>
      <td class="num">${option.delta}</td>
      <td class="num">${option.gamma}</td>
      <td class="num">${option.vega}</td>
      <td class="num">${option.theta}</td>
    </tr>
  `).join("");
  drawSurface(document.getElementById("surfaceChart"), snapshot.surfaces);
}

function renderRisk(risk) {
  document.getElementById("mvMetric").textContent = money.format(risk.totals.market_value);
  document.getElementById("deltaMetric").textContent = money.format(risk.totals.delta);
  document.getElementById("vegaMetric").textContent = money.format(risk.totals.vega);
  document.getElementById("worstMetric").textContent = money.format(risk.worstCase.pnl);
  document.getElementById("riskTable").innerHTML = risk.lines.map((line) => `
    <tr>
      <td>${line.symbol}</td>
      <td class="num">${line.quantity}</td>
      <td class="num">${money.format(line.marketPrice)}</td>
      <td class="num ${line.marketValue < 0 ? "negative" : "positive"}">${money.format(line.marketValue)}</td>
      <td class="num">${money.format(line.delta)}</td>
      <td class="num">${line.gamma}</td>
      <td class="num">${money.format(line.vega)}</td>
      <td class="num">${money.format(line.theta)}</td>
    </tr>
  `).join("");
  drawStress(document.getElementById("stressChart"), risk.scenarios);
  drawBars(document.getElementById("concentrationChart"), risk.concentration, "weight");
}

function renderOrders(orders) {
  document.getElementById("ordersTable").innerHTML = orders.map((order) => `
    <tr>
      <td>${order.createdAt}</td>
      <td>${order.status}</td>
      <td>${JSON.stringify(order.payload.ticket || order.payload).slice(0, 140)}</td>
    </tr>
  `).join("");
  drawOrderTimeline(document.getElementById("ordersChart"), orders);
}

async function submitOrder(kind) {
  const form = new FormData(document.getElementById("orderForm"));
  const payload = {
    symbol: form.get("symbol"),
    action: form.get("action"),
    quantity: Number(form.get("quantity")),
    orderType: form.get("orderType"),
    limitPrice: form.get("limitPrice"),
    transmit: form.get("transmit") === "on",
  };
  const path = kind === "preview" ? "/api/orders/preview" : "/api/orders/place";
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const result = await response.json();
  document.getElementById("orderResult").textContent = JSON.stringify(result, null, 2);
  const orders = await getJson("/api/orders");
  renderOrders(orders.orders);
}

function drawSurface(canvas, points) {
  const ctx = setupCanvas(canvas);
  title(ctx, "IV smile: strike vs implied volatility");
  if (!points.length) {
    empty(ctx, canvas, "No option surface available");
    return;
  }
  const sample = points.filter((point) => point.underlying === points[0].underlying);
  const strikes = sample.map((point) => point.strike);
  const ivs = sample.map((point) => point.implied_vol * 100);
  drawAxes(ctx, canvas);
  polyline(ctx, canvas, strikes, ivs, "#1b5fb8");
  scatter(ctx, canvas, strikes, ivs, "#00856f");
}

function drawStress(canvas, scenarios) {
  const ctx = setupCanvas(canvas);
  title(ctx, "Scenario heatmap: spot shock / vol shock");
  const spot = [...new Set(scenarios.map((item) => item.spotShift))].sort((a, b) => a - b);
  const vol = [...new Set(scenarios.map((item) => item.volShift))].sort((a, b) => a - b);
  const values = scenarios.map((item) => item.pnl);
  const maxAbs = Math.max(...values.map(Math.abs), 1);
  const cellW = (canvas.width - 120) / spot.length;
  const cellH = (canvas.height - 95) / vol.length;
  vol.forEach((volShift, row) => {
    spot.forEach((spotShift, col) => {
      const item = scenarios.find((candidate) => candidate.spotShift === spotShift && candidate.volShift === volShift);
      const intensity = Math.min(Math.abs(item.pnl) / maxAbs, 1);
      ctx.fillStyle = item.pnl >= 0 ? `rgba(0, 133, 111, ${0.18 + intensity * 0.72})` : `rgba(180, 35, 24, ${0.18 + intensity * 0.72})`;
      ctx.fillRect(70 + col * cellW, 48 + row * cellH, cellW - 3, cellH - 3);
      ctx.fillStyle = "#15202b";
      ctx.font = "12px sans-serif";
      ctx.fillText(Math.round(item.pnl).toString(), 78 + col * cellW, 72 + row * cellH);
    });
  });
  ctx.fillStyle = "#697586";
  spot.forEach((value, col) => ctx.fillText(`${value}%`, 78 + col * cellW, canvas.height - 20));
  vol.forEach((value, row) => ctx.fillText(`${value}v`, 20, 70 + row * cellH));
}

function drawBars(canvas, rows, field) {
  const ctx = setupCanvas(canvas);
  title(ctx, "Concentration by absolute market value");
  if (!rows.length) {
    empty(ctx, canvas, "No positions");
    return;
  }
  const maxValue = Math.max(...rows.map((row) => row[field]), 0.01);
  const barHeight = 24;
  rows.forEach((row, index) => {
    const y = 55 + index * 34;
    const width = (canvas.width - 190) * row[field] / maxValue;
    ctx.fillStyle = "#1b5fb8";
    ctx.fillRect(150, y, width, barHeight);
    ctx.fillStyle = "#15202b";
    ctx.font = "13px sans-serif";
    ctx.fillText(row.symbol, 18, y + 17);
    ctx.fillText(`${Math.round(row[field] * 100)}%`, 158 + width, y + 17);
  });
}

function drawOrderTimeline(canvas, orders) {
  const ctx = setupCanvas(canvas);
  title(ctx, "Order audit trail");
  if (!orders.length) {
    empty(ctx, canvas, "No order events yet");
    return;
  }
  const step = (canvas.width - 120) / Math.max(orders.length - 1, 1);
  orders.slice().reverse().forEach((order, index) => {
    const x = 60 + index * step;
    ctx.fillStyle = order.status === "blocked" ? "#b42318" : "#1b5fb8";
    ctx.beginPath();
    ctx.arc(x, 120, 9, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = "#697586";
    ctx.font = "12px sans-serif";
    ctx.fillText(order.status, x - 20, 148);
  });
}

function setupCanvas(canvas) {
  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  return ctx;
}

function title(ctx, text) {
  ctx.fillStyle = "#15202b";
  ctx.font = "16px sans-serif";
  ctx.fillText(text, 18, 25);
}

function drawAxes(ctx, canvas) {
  ctx.strokeStyle = "#d8dee7";
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(55, 40);
  ctx.lineTo(55, canvas.height - 35);
  ctx.lineTo(canvas.width - 20, canvas.height - 35);
  ctx.stroke();
}

function polyline(ctx, canvas, xs, ys, color) {
  const scale = scales(canvas, xs, ys);
  ctx.strokeStyle = color;
  ctx.lineWidth = 3;
  ctx.beginPath();
  xs.forEach((x, index) => {
    const point = scale(x, ys[index]);
    if (index === 0) ctx.moveTo(point.x, point.y);
    else ctx.lineTo(point.x, point.y);
  });
  ctx.stroke();
}

function scatter(ctx, canvas, xs, ys, color) {
  const scale = scales(canvas, xs, ys);
  ctx.fillStyle = color;
  xs.forEach((x, index) => {
    const point = scale(x, ys[index]);
    ctx.beginPath();
    ctx.arc(point.x, point.y, 4, 0, Math.PI * 2);
    ctx.fill();
  });
}

function scales(canvas, xs, ys) {
  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  const minY = Math.min(...ys);
  const maxY = Math.max(...ys);
  return (x, y) => ({
    x: 60 + (x - minX) / Math.max(maxX - minX, 1) * (canvas.width - 90),
    y: canvas.height - 40 - (y - minY) / Math.max(maxY - minY, 1) * (canvas.height - 90),
  });
}

function empty(ctx, canvas, text) {
  ctx.fillStyle = "#697586";
  ctx.font = "15px sans-serif";
  ctx.fillText(text, 24, canvas.height / 2);
}

refreshAll();
